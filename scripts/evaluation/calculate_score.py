import re
import argparse
import enchant
import nltk
from nltk.corpus import wordnet as wn
from threading import Lock
import torch
from fastDamerauLevenshtein import damerauLevenshtein
from sentence_transformers import SentenceTransformer


class Evaluation:
    def __init__(self, config):
        self.config = config
        self.use_custom_rules = True
        self.similarity_threshold = 0.8
        self.use_embedding_fallback = getattr(config, 'USE_EMBEDDING_FALLBACK', False)
        self.embedding_threshold = getattr(config, 'EMBEDDING_THRESHOLD', 0.75)
        self.enchant_lock = Lock()

        self._embedding_model = None
        if self.use_embedding_fallback:
            try:
                print("Loading Qwen3 embedding model during initialization...")
                device = 'cuda' if torch.cuda.is_available() else 'cpu'
                print(f"Using device: {device}")
                self._embedding_model = SentenceTransformer(
                    'Qwen3-Embedding-8B',
                    trust_remote_code=True,
                    device=device
                )
                print(f"Qwen3 embedding model loaded successfully on {device}")
            except ImportError as e:
                print(f"sentence-transformers not installed: {str(e)}")
                print("Install with: pip install sentence-transformers")
                self.use_embedding_fallback = False
            except Exception as e:
                print(f"Failed to load Qwen3 embedding model: {str(e)}")
                print(f"Full error: {repr(e)}")
                self.use_embedding_fallback = False
                self._embedding_model = None

        self.us_dictionary = enchant.Dict("en_US")
        self.gb_dictionary = enchant.Dict("en_GB")

        self.custom_true_synonyms = {
            frozenset(['clear', 'free', 'unblock']),
            frozenset(['run', 'execute', 'start', 'launch']),
        }

        self.custom_false_synonyms = {
            frozenset(['find', 'name']),
            frozenset(['create', 'destroy']),
        }

    def check_word_similarity(self, correct_name, inferred_name):
        if len(correct_name) == 0 and len(inferred_name) == 0:
            return True
        if len(correct_name) == 0 or len(inferred_name) == 0:
            return False
        if correct_name.strip() == inferred_name.strip():
            return True

        edit_sim = damerauLevenshtein(correct_name, inferred_name, similarity=True)
        if edit_sim >= self.config.EDIT_DISTANCE_THRESHOLD:
            return True

        stemmer = nltk.stem.PorterStemmer()
        lemmatiser = nltk.WordNetLemmatizer()

        inferred_words = [inferred_name]
        correct_words = [correct_name]
        inferred_stems = {stemmer.stem(w) for w in inferred_words} | set(inferred_words)
        correct_stems = {stemmer.stem(w) for w in correct_words} | set(correct_words)
        inferred_lemmas = set()
        correct_lemmas = set()

        for word in inferred_words:
            if self.us_dictionary.check(word):
                inferred_lemmas.add(lemmatiser.lemmatize(word, pos='v'))
        for word in correct_words:
            if self.us_dictionary.check(word):
                correct_lemmas.add(lemmatiser.lemmatize(word, pos='v'))

        if inferred_lemmas and correct_lemmas:
            jaccard = len(inferred_lemmas & correct_lemmas) / len(inferred_lemmas | correct_lemmas)
            if jaccard >= 0.6:
                return True

        if inferred_stems and correct_stems:
            jaccard = len(inferred_stems & correct_stems) / len(inferred_stems | correct_stems)
            if jaccard >= 0.6:
                return True

        try:
            if len(correct_name)>=3 and len(inferred_name) >=3:
                inferred_pos = nltk.pos_tag([inferred_name])[0][1]
                correct_pos = nltk.pos_tag([correct_name])[0][1]
                if inferred_pos not in ('IN', 'TO') and correct_pos not in ('IN', 'TO'):
                    clean_full_form = re.sub(r'[_\-]', ' ', correct_name)
                    clean_abbrev_form = re.sub(r'[_\-]', ' ', inferred_name)
                    abbreviation_matcher = AbbreviationMatcher(
                        match_score=2,
                        mismatch_penalty=-2,
                        gap_in_full_penalty=-1.0,
                        gap_in_abbrev_penalty=-3,
                        case_sensitive=False,
                        strip_spaces=True
                    )
                    forward_match = abbreviation_matcher.match(full_form=clean_full_form, abbreviation=clean_abbrev_form)
                    reverse_match = abbreviation_matcher.match(full_form=clean_abbrev_form, abbreviation=clean_full_form)
                    valid_match1 = forward_match.confidence>=0.65 and (len(forward_match.match_positions>=3))
                    valid_match2 = reverse_match.confidence >= 0.65 and (len(reverse_match.match_positions >= 3))
                    if valid_match1 or valid_match2:
                        return True
        except Exception as e:
            print(f"Abbreviation matching failed: {str(e)}")

        if correct_name.startswith(inferred_name) or inferred_name.startswith(correct_name):
            if len(correct_name) > 1 and len(inferred_name) > 1:
                return True

        if len(correct_name) < 3 or len(inferred_name) < 3:
            return False

        if self.use_embedding_fallback:
            if self._check_embedding_similarity(correct_name, inferred_name):
                return True
        return False

    def _check_embedding_similarity(self, s1, s2):
        try:
            if self._embedding_model is None:
                print("Embedding model not available for similarity check")
                return False
            embeddings = self._embedding_model.encode(
                [s1, s2],
                convert_to_tensor=True,
                show_progress_bar=False,
                normalize_embeddings=True
            )
            a, b = embeddings[0], embeddings[1]
            similarity = torch.dot(a, b).item()
            return similarity >= self.embedding_threshold
        except Exception as e:
            print(f"Embedding check failed for '{s1}' vs '{s2}': {str(e)}")
            print(f"Full error details: {repr(e)}")
            return False

    def get_correct_predictions_similarity(self, target, prediction):
        true_pos, false_pos, false_neg = 0, 0, 0
        word_replacements = dict()
        matched_indices = set()
        skip_words = ['from', 'and', 'for', 'the']

        for j, pred_word in enumerate(prediction):
            if pred_word in target:
                matched_indices.add(j)

        for i, target_word in enumerate(target):
            for j, pred_word in enumerate(prediction):
                if pred_word in skip_words or target_word in skip_words:
                    continue
                if target_word != pred_word and j not in word_replacements and j not in matched_indices:
                    if self.check_word_similarity(target_word, pred_word):
                        word_replacements[j] = target_word

        for index, replacement_word in word_replacements.items():
            prediction[index] = replacement_word

        if target == prediction:
            true_pos = len(target)
        else:
            target = set(target)
            prediction = set(prediction)
            true_pos += len(target.intersection(prediction))
            false_neg += len(target.difference(prediction))
            false_pos += len(prediction.difference(target))

        return true_pos, false_pos, false_neg


class AbbreviationMatcher:
    def __init__(self,
                 match_score=2,
                 mismatch_penalty=-2,
                 gap_in_full_penalty=-1.0,
                 gap_in_abbrev_penalty=-3,
                 case_sensitive=False,
                 strip_spaces=True):
        self.match_score = match_score
        self.mismatch_penalty = mismatch_penalty
        self.gap_in_full_penalty = gap_in_full_penalty
        self.gap_in_abbrev_penalty = gap_in_abbrev_penalty
        self.case_sensitive = case_sensitive
        self.strip_spaces = strip_spaces
        self._cache = {}
        self._match_history = []

    def _preprocess(self, text):
        if not self.case_sensitive:
            text = text.lower()
        if self.strip_spaces:
            text = text.replace(" ", "")
        return text

    def _get_scoring_function(self):
        def score_chars(char1, char2):
            if char1 == char2:
                return self.match_score
            return self.mismatch_penalty

        return score_chars

    def _fill_dp_matrix(self, full, abbrev):
        m, n = len(full), len(abbrev)
        dp_matrix = [[0] * (n + 1) for _ in range(m + 1)]
        traceback = [[0] * (n + 1) for _ in range(m + 1)]
        score_func = self._get_scoring_function()
        max_score = 0
        max_pos = (0, 0)

        for i in range(1, m + 1):
            for j in range(1, n + 1):
                match_score = dp_matrix[i - 1][j - 1] + score_func(full[i - 1], abbrev[j - 1])
                skip_in_full = dp_matrix[i - 1][j] + self.gap_in_full_penalty
                skip_in_abbrev = dp_matrix[i][j - 1] + self.gap_in_abbrev_penalty
                scores = [0, match_score, skip_in_full, skip_in_abbrev]
                dp_matrix[i][j] = max(scores)
                traceback[i][j] = scores.index(dp_matrix[i][j])

                if dp_matrix[i][j] > max_score:
                    max_score = dp_matrix[i][j]
                    max_pos = (i, j)

        return dp_matrix, traceback, max_score, max_pos

    def _traceback(self, dp_matrix, traceback, full, abbrev, max_pos):
        i, j = max_pos
        match_full, match_abbrev = [], []
        matches = []

        while i > 0 and j > 0 and dp_matrix[i][j] > 0:
            if traceback[i][j] == 1:
                match_full.append(full[i - 1])
                match_abbrev.append(abbrev[j - 1])
                matches.append((i - 1, j - 1))
                i -= 1
                j -= 1
            elif traceback[i][j] == 2:
                match_full.append(full[i - 1])
                match_abbrev.append('-')
                i -= 1
            elif traceback[i][j] == 3:
                match_full.append('-')
                match_abbrev.append(abbrev[j - 1])
                j -= 1

        match_full.reverse()
        match_abbrev.reverse()
        return ''.join(match_full), ''.join(match_abbrev), matches

    def _calculate_confidence(self, score, abbrev_length):
        if abbrev_length == 0:
            return 0.0
        max_possible_score = abbrev_length * self.match_score
        return min(1.0, score / max_possible_score)

    def match(self, full_form, abbreviation):
        cache_key = (full_form, abbreviation,
                     self.match_score, self.mismatch_penalty,
                     self.gap_in_full_penalty, self.gap_in_abbrev_penalty,
                     self.case_sensitive, self.strip_spaces)

        if cache_key in self._cache:
            return self._cache[cache_key]

        clean_full = self._preprocess(full_form)
        clean_abbrev = self._preprocess(abbreviation)
        dp_matrix, traceback, max_score, max_pos = self._fill_dp_matrix(clean_full, clean_abbrev)
        full_match, abbrev_match, matches = self._traceback(
            dp_matrix, traceback, clean_full, clean_abbrev, max_pos
        )
        confidence = self._calculate_confidence(max_score, len(clean_abbrev))

        result = MatchResult(
            full_form=full_form,
            abbreviation=abbreviation,
            score=max_score,
            full_match=full_match,
            abbrev_match=abbrev_match,
            match_positions=matches,
            confidence=confidence,
            dp_matrix=dp_matrix,
            traceback_matrix=traceback
        )

        self._cache[cache_key] = result
        self._match_history.append(result)
        return result

    def batch_match(self, full_forms, abbreviations):
        results = []
        for full, abbrev in zip(full_forms, abbreviations):
            results.append(self.match(full, abbrev))
        return results

    def find_best_match(self, abbreviation, candidate_full_forms):
        best_result = None
        all_results = []

        for full_form in candidate_full_forms:
            result = self.match(full_form, abbreviation)
            all_results.append(result)
            if best_result is None or result.score > best_result.score:
                best_result = result

        return best_result, all_results

    def set_scoring_params(self, match_score=None, mismatch_penalty=None,
                           gap_in_full_penalty=None, gap_in_abbrev_penalty=None):
        if match_score is not None:
            self.match_score = match_score
        if mismatch_penalty is not None:
            self.mismatch_penalty = mismatch_penalty
        if gap_in_full_penalty is not None:
            self.gap_in_full_penalty = gap_in_full_penalty
        if gap_in_abbrev_penalty is not None:
            self.gap_in_abbrev_penalty = gap_in_abbrev_penalty
        self._cache.clear()

    def clear_cache(self):
        self._cache.clear()

    def get_match_history(self, limit=None):
        if limit is None:
            return self._match_history
        return self._match_history[-limit:]


class MatchResult:
    def __init__(self, full_form, abbreviation, score,
                 full_match, abbrev_match, match_positions,
                 confidence, dp_matrix=None, traceback_matrix=None):
        self.full_form = full_form
        self.abbreviation = abbreviation
        self.score = score
        self.full_match = full_match
        self.abbrev_match = abbrev_match
        self.match_positions = match_positions
        self.confidence = confidence
        self.dp_matrix = dp_matrix
        self.traceback_matrix = traceback_matrix
        self.match_length = len(full_match.replace('-', ''))
        self.abbrev_length = len(abbreviation.replace(' ', ''))

    def __repr__(self):
        return (f"MatchResult(score={self.score:.2f}, "
                f"confidence={self.confidence:.2%}, "
                f"match='{self.full_match[:20]}...')")

    def to_dict(self):
        return {
            'full_form': self.full_form,
            'abbreviation': self.abbreviation,
            'score': self.score,
            'full_match': self.full_match,
            'abbrev_match': self.abbrev_match,
            'match_positions': self.match_positions,
            'confidence': self.confidence,
            'match_length': self.match_length,
            'abbrev_length': self.abbrev_length
        }

    def print_alignment(self, show_original=False):
        if show_original:
            print(f"Original Full:  {self.full_form}")
            print(f"Original Abbrev: {self.abbreviation}")
            print("-" * 50)
        print(f"Matching Score: {self.score:.2f}")
        print(f"Confidence: {self.confidence:.2%}")
        print()
        print(f"Aligned Full:  {self.full_match}")
        print(f"Aligned Abbrev: {self.abbrev_match}")
        match_indicator = []

        for f, a in zip(self.full_match, self.abbrev_match):
            if f == a and f != '-':
                match_indicator.append('|')
            elif f != '-' and a != '-':
                match_indicator.append(':')
            else:
                match_indicator.append(' ')

        print(f"Match:        {''.join(match_indicator)}")

    def get_match_summary(self):
        return {
            'score': self.score,
            'confidence': self.confidence,
            'is_good_match': self.confidence > 0.7,
            'match_ratio': len(self.full_match.replace('-', '')) / len(self.full_form.replace(' ', '')),
            'gap_count': self.full_match.count('-') + self.abbrev_match.count('-')
        }


def split_words(name):

    return name.lower().strip().split(' ')


def calculate_results(true_pos, false_pos, false_neg):
    if true_pos + false_pos == 0:
        return 0, 0, 0
    precision = true_pos / (true_pos + false_pos)
    recall = true_pos / (true_pos + false_neg)

    if precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0

    return precision, recall, f1


def main(args):
    input_file = args.input_file

    class Config:
        EDIT_DISTANCE_THRESHOLD = 0.75
        USE_ABBREVIATION_MATCHING = True
        USE_EMBEDDING_FALLBACK = True
        EMBEDDING_THRESHOLD = 0.8

    evaluation = Evaluation(Config())

    true_pos, false_pos, false_neg = 0, 0, 0
    total = 0
    targets = []
    predictions = []

    try:
        with open(input_file, 'r') as f:
            for i, line in enumerate(f):
                total += 1
                line = line.strip('\n')
                lines = line.split(',')

                assert isinstance(lines[1], str) and len(lines[1]) > 0, "Don't give empty prediction"
                targets.append(lines[0])
                predictions.append(lines[1])
                target = split_words(lines[0])
                prediction = split_words(lines[1])

                tp, fp, fn = evaluation.get_correct_predictions_similarity(target, prediction)
                true_pos += tp
                false_pos += fp
                false_neg += fn

        precision, recall, f1 = calculate_results(true_pos, false_pos, false_neg)
        print(f"Probability Precision: {precision:.6f}, Recall: {recall:.6f}, F1: {f1:.6f}")

    except Exception as e:
        print(f"Error processing file {input_file}: {e}")
        print("Probability Precision: 0.000000, Recall: 0.000000, F1: 0.000000")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Evaluate function name prediction using the provided input file')
    parser.add_argument('-i', '--input_file', type=str, required=True,
                        help='Path to the evaluation input file.')
    args = parser.parse_args()

    main(args)