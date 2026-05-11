import json
import os
import subprocess
import re
import argparse


def run_cmd(cmd, timeout=3):
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        return "[Timeout] Command took too long."
    except Exception as e:
        return str(e)


class TextSanitizer:
    def __init__(self):
        self.gibberish_threshold = 0.65
        max_repeat = 3
        self.placeholder_patterns = [
            r"%[dlu]",
            r"0x[0-9a-fA-F]+",
            r"^[\W\d_]+$",
            r'(.)\1{' + str(max_repeat) + ',}',
            r'^[0-9]{8,}$',
            r'^[.%$#&*+=\-_]{3,}$',
            r'GCC:\s*\([^)]+\)\s*\d+\.\d+\.\d+',
            r'GCC:\s*\d+\.\d+\.\d+',
            r'GCC:.*?\d+\.\d+(?:\.\d+)*',
            r'^[a-zA-Z0-9]*[/~&@$>+=*\-;]+[a-zA-Z0-9]*$',
            r'.*[/~&@$>+=*\-;]{4,}.*'
        ]
        self.ignored_strings = [
            '.resources.dll', '.shstrtab', '.note.gnu.build-id', '.gnu.hash',
            '.gnu.version', '.gnu.version_r', '.rel.dyn', '.rel.plt', '.plt.got',
            '.eh_frame_hdr', '.eh_frame', '.init_array', '.fini_array', '.data.rel.ro',
            '.dynamic', '.got.plt', '.comment', '.note.ABI-tag'
        ]
        self.common_sequences = [
            "0123456789",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "abcdefghijklmnopqrstuvwxyz",
        ]
        self.url_patterns = [
            r'https?://[^\s/$.?#].[^\s]*',
            r'ftp://[^\s/$.?#].[^\s]*',
            r'file://[^\s]*',
            r'www\.[^\s/$.?#].[^\s]*',
            r'[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})?[/\s]'
        ]
        self.language_names = [
            "Abkhazian", "Achinese", "Afrikaans", "Aragonese", "Old English",
            "Mapudungun", "Assamese", "Asturian", "Azerbaijani", "Balinese",
            "Belarusian", "Bulgarian", "Bhojpuri", "Buginese", "Chamorro",
            "Corsican", "Crimean Tatar", "Kashubian", "Church Slavic", "Lower Sorbian",
            "Dzongkha", "Esperanto", "Estonian", "Filipino", "Friulian",
            "Western Frisian", "Scottish Gaelic", "Galician", "Swiss German",
            "Gujarati", "Hiligaynon", "Hiri Motu", "Croatian", "Upper Sorbian",
            "Hungarian", "Armenian", "Interlingua", "Indonesian", "Interlingue",
            "Sichuan Yi", "Icelandic", "Inuktitut", "Japanese", "Javanese",
            "Georgian", "Kabardian", "Kuanyama", "Kalaallisut", "Central Khmer",
            "Kimbundu", "Kashmiri", "Letzeburgesch", "Limburgish", "Lithuanian",
            "Luba-Katanga", "Luba-Lulua", "Madurese", "Maithili", "Mandingo",
            "Malagasy", "Marshallese", "Minangkabau", "Macedonian", "Malayalam",
            "Mongolian", "Manipuri", "Moldavian", "Neapolitan", "Norwegian Bokmal",
            "North Ndebele", "Low Saxon", "Norwegian Nynorsk", "Norwegian",
            "South Ndebele", "Northern Sotho", "Nyamwezi", "Nyankole", "(Afan) Oromo",
            "Ossetian", "Pangasinan", "Pampanga", "Papiamento", "Portuguese",
            "Rajasthani", "Romanian", "Kinyarwanda", "Sanskrit", "Sardinian",
            "Sicilian", "Northern Sami", "Slovenian", "Southern Sami", "Lule Sami",
            "Inari Sami", "Skolt Sami", "Albanian", "Sundanese", "Tigrinya",
            "Setswana", "Tahitian", "Ukrainian", "Vietnamese", "Austrian",
            "English (British)", "Argentinian", "Spanish (Canary Islands)",
            "Brazilian Portuguese", "Chinese (simplified)", "Chinese (Hong Kong)",
            "Chinese (traditional)"
        ]
        self.path_patterns = [
            r'^/(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+(?:\.[a-zA-Z0-9]+)?$',
            r'^[a-zA-Z]:\\(?:[a-zA-Z0-9_.-]+\\)*[a-zA-Z0-9_.-]+(?:\.[a-zA-Z0-9]+)?$',
            r'^\.{1,2}/(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+(?:\.[a-zA-Z0-9]+)?$',
            r'^(?:[a-zA-Z0-9_.-]+/)+[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]+$',
            r'^(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+\.(?:py|js|java|cpp|c|txt|json|xml|html|css|md)$'
        ]

    def is_url(self, text):
        text = text.strip()
        for pattern in self.url_patterns:
            if re.match(pattern, text, re.IGNORECASE):
                return True
        return False

    def text_quality_score(self, text):
        """Calculate text quality score (0-1)"""
        if not text.strip():
            return 1.0

        total_chars = len(text)
        alnum_ratio = sum(1 for c in text if c.isalnum()) / total_chars
        special_chars = '][%|/~&@$>+=*;-<'
        special_ratio = sum(1 for c in text if c in special_chars) / total_chars

        consecutive_penalty = 1.0
        if re.search(r'[/~&@$>+=*\-|%;<]{3,}', text):
            consecutive_penalty = 0.3

        score = (alnum_ratio * 0.7 + (1 - special_ratio) * 0.3) * consecutive_penalty
        return score

    def sanitize_text(self, lines):
        seen_strings = set()
        valid_lines = []
        path_strings = set()
        url_strings = set()

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line in self.language_names:
                continue

            if line in self.ignored_strings:
                continue

            if any(re.search(p, line) for p in self.placeholder_patterns):
                continue

            if any(sequence in line for sequence in self.common_sequences):
                seen_strings.add(line)
                continue

            if line in seen_strings:
                continue

            if self.is_url(line):
                url_strings.add(line)
                continue

            score = self.text_quality_score(line)
            if score < self.gibberish_threshold:
                continue

            if any(re.search(p, line) for p in self.path_patterns):
                path_strings.add(line)
                seen_strings.add(line)
            else:
                seen_strings.add(line)
                valid_lines.append(line)

        if len(valid_lines) + len(path_strings) < 100:
            cleaned_lines = list(path_strings) + valid_lines + list(url_strings)
        else:
            cleaned_lines = list(path_strings) + valid_lines

        return cleaned_lines


def analyze_binary(binary_path):
    # Check file permissions
    try:
        if not os.access(binary_path, os.X_OK):
            print(f"warning: file {binary_path} can not access!!")
    except Exception as e:
        print(f"error: {e}")

    def clean_file_output(text):
        """Remove file path prefix from file command output"""
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(r'^[^:]+:\s*', '', line)
            lines.append(line)
        return "\n".join(lines)

    file_output = run_cmd(f"file {binary_path}")
    file_output = clean_file_output(file_output)
    ldd_output = run_cmd(f"ldd {binary_path}")

    def deduplicate_text(text):
        seen = set()
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                lines.append(line)
        return "\n".join(lines)

    file_output = deduplicate_text(file_output)
    ldd_output = deduplicate_text(ldd_output)
    strings_output = run_cmd(f"strings -n 8 {binary_path}")

    strings_lines = strings_output.splitlines()
    sanitizer = TextSanitizer()
    filtered_lines = sanitizer.sanitize_text(strings_lines)
    strings_truncated = filtered_lines

    return {
        "file_output": file_output,
        "ldd_output": ldd_output,
        "strings_out": strings_truncated
    }



def process_binary(stripped_binary_path, out_path):
    outdata = {}
    
    bin_name = stripped_binary_path.split('/')[-1]

    try:
        print(f"Analyzing: {stripped_binary_path}")
        analysis = analyze_binary(stripped_binary_path)

        outdata[bin_name] = analysis
        
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(outdata, f, indent=4, ensure_ascii=False)
       
    except Exception as e:
        print(f"Error analyzing: {stripped_binary_path}, error_info: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process stripped binaries and extract binary information.')
    parser.add_argument('-i', '--input_path', type=str, required=True,
                        help='Path containing stripped binaries.')
    parser.add_argument('-o', '--output_path', type=str, required=True,
                        help='Output file path.')
    args = parser.parse_args()

    process_binary(stripped_binary_path=args.input_path, out_path=args.output_path)
