import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Optional


ENTROPY = {
    "english_prose": (3.8, 4.5),
    "informal_text": (4.2, 4.8),
    "code_with_comments": (4.4, 5.0),
    "pure_code": (4.8, 5.3),
    "html_mixed": (4.6, 5.2),
    "html_heavy": (5.0, 5.5),
    "minified": (5.4, 5.8),
    "base64": (5.95, 6.02),
    "hex_encoded": (3.5, 3.7),
    "random_ascii": (6.4, 6.6),
    "compressed": (7.5, 8.0),
}

IC = {
    "english": (0.064, 0.072),
    "english_informal": (0.058, 0.066),
    "code_english_vars": (0.048, 0.058),
    "code_terse": (0.042, 0.050),
    "html": (0.048, 0.062),
    "multilingual": (0.042, 0.052),
    "random": (0.036, 0.042),
    "base64": (0.015, 0.020),
}

CHAR_RATIOS = {
    "prose": {
        "alpha": (0.75, 0.88),
        "space": (0.12, 0.20),
        "special": (0.01, 0.05),
    },
    "code": {
        "alpha": (0.45, 0.65),
        "space": (0.10, 0.25),
        "special": (0.08, 0.18),
    },
    "html": {
        "alpha": (0.50, 0.70),
        "special": (0.12, 0.25),
    },
    "data": {
        "alpha": (0.30, 0.55),
        "special": (0.15, 0.30),
    },
}

LINE_STATS = {
    "prose_avg_len": (45, 85),
    "code_avg_len": (25, 50),
    "minified_line_len": 500,
    "boilerplate_unique_threshold": 0.70,
    "severe_boilerplate_threshold": 0.40,
}

DIAGNOSTIC_BIGRAMS = {
    "html": ["</", "/>", "=\"", "><", "iv", "ss", "la"],
    "code": ["if", "fo", "wh", "re", "de", "fu", "=>", "();", "{}"],
    "json": ["\":", ",\"", "{\"", "\"}", "[]", "nu", "tr", "fa"],
    "markdown": ["# ", "##", "**", "- ", "```", "](", "!["],
    "base64": ["==", "AA", "BB", "CC", "QQ", "ww", "xx"],
}

GARBAGE_PATTERNS = [
    r"utm_source=",
    r"(?:__|ga|_gaq|gtag|fbq)\s*[(\[]",
    r"data:image/[^;]+;base64,",
    r"\.(?:woff2?|ttf|eot)\b",
    r"@keyframes\s+\w+",
    r"(?:cookie|gdpr|consent|privacy).{0,30}(?:accept|agree|policy)",
    r"(?:subscribe|newsletter|signup).{0,20}(?:email|inbox)",
    r"\\u[0-9a-fA-F]{4}",
    r"(?:prev|next|older|newer)\s*(?:post|page|article)",
    r"(?:share|tweet|pin)\s*(?:on|this|it)",
    r"all\s*rights?\s*reserved",
]

QUALITY_SIGNALS = [
    r"\b(?:because|therefore|however|although|furthermore|consequently)\b",
    r"\b(?:study|research|data|evidence|analysis|found|shows)\b",
    r"\b(?:first|second|third|finally|additionally|moreover)\b",
    r"\b\d{4}\b",
    r"(?:Dr\.|Prof\.|Ph\.?D|University|Institute)\b",
]


@dataclass(frozen=True)
class TextDigest:
    chars: int
    lines: int
    entropy: float
    entropy_verdict: str
    ic: float
    ic_verdict: str
    alpha_pct: float
    digit_pct: float
    space_pct: float
    special_pct: float
    avg_line_len: int
    max_line_len: int
    unique_line_pct: float
    primary_type: str
    confidence: float
    type_scores: str
    info_density: float
    prose_quality: float
    garbage_pct: float
    boilerplate_pct: float
    verdict: str
    action: str
    flags: str
    best_sample: str

    def summary_line(self) -> str:
        parts = [
            f"type={self.primary_type}",
            f"conf={self.confidence:.2f}",
            f"verdict={self.verdict}",
            f"action={self.action}",
            f"info={self.info_density:.2f}",
            f"garbage={self.garbage_pct:.2f}",
            f"boiler={self.boilerplate_pct:.2f}",
        ]
        if self.flags:
            parts.append(f"flags={self.flags}")
        return " ".join(parts)

    def to_prompt(self) -> str:
        return (
            "<digest>\n"
            f"{self.chars:,} chars | {self.lines} lines | "
            f"avg_line: {self.avg_line_len} | max_line: {self.max_line_len}\n"
            f"entropy: {self.entropy:.2f} -> {self.entropy_verdict} | "
            f"ic: {self.ic:.4f} -> {self.ic_verdict}\n"
            f"chars: a:{self.alpha_pct:.0%} d:{self.digit_pct:.0%} "
            f"s:{self.space_pct:.0%} sp:{self.special_pct:.0%}\n"
            f"unique_lines: {self.unique_line_pct:.0%} | "
            f"boilerplate: {self.boilerplate_pct:.0%} | "
            f"garbage: {self.garbage_pct:.0%}\n"
            f"type: {self.primary_type} ({self.confidence:.0%}) | {self.type_scores}\n"
            f"quality: info_density={self.info_density:.2f} prose={self.prose_quality:.2f}\n"
            f"VERDICT: {self.verdict} -> {self.action}\n"
            f"{f'flags: {self.flags}' if self.flags else ''}\n"
            f"sample: \"{self.best_sample}\"\n"
            "</digest>"
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class Digestor:
    GARBAGE_RE = [re.compile(p, re.I) for p in GARBAGE_PATTERNS]
    QUALITY_RE = [re.compile(p, re.I) for p in QUALITY_SIGNALS]

    HTML_CHARS = frozenset("<>/\"'=")
    CODE_CHARS = frozenset("{}();[]=>:")
    MD_CHARS = frozenset("#*_`[]()>!")
    DATA_CHARS = frozenset("{}[]\":,")

    def __init__(self) -> None:
        self._log_table = {i: math.log2(i) for i in range(1, 10001)}

    def digest(self, text: str) -> TextDigest:
        if not text:
            return self._empty()

        n = len(text)
        freq: Counter = Counter()
        alpha = digit = space = upper = 0
        html_c = code_c = md_c = data_c = 0

        for ch in text:
            freq[ch] += 1
            if ch.isalpha():
                alpha += 1
                if ch.isupper():
                    upper += 1
            elif ch.isdigit():
                digit += 1
            elif ch.isspace():
                space += 1

            if ch in self.HTML_CHARS:
                html_c += 1
            if ch in self.CODE_CHARS:
                code_c += 1
            if ch in self.MD_CHARS:
                md_c += 1
            if ch in self.DATA_CHARS:
                data_c += 1

        special = n - alpha - digit - space

        entropy = self._entropy(freq, n)
        entropy_verdict = self._classify_entropy(entropy)

        alpha_freq = Counter(ch.lower() for ch in text if ch.isalpha())
        ic = self._ic(alpha_freq)
        ic_verdict = self._classify_ic(ic)

        lines = text.split("\n")
        line_count = len(lines)
        line_lens = [len(l) for l in lines]
        avg_line = sum(line_lens) // max(1, line_count)
        max_line = max(line_lens) if line_lens else 0

        sample_lines = lines[:10000]
        normalized = [l.strip().lower() for l in sample_lines if len(l.strip()) > 5]
        unique_pct = len(set(normalized)) / max(1, len(normalized)) if normalized else 1.0

        scores = self._score_types(
            text[:50000],
            n,
            entropy,
            ic,
            alpha / n,
            special / n,
            html_c / n,
            code_c / n,
            md_c / n,
            data_c / n,
            avg_line,
            max_line,
            unique_pct,
        )

        primary = max(scores, key=scores.get)
        confidence = scores[primary]

        sorted_scores = sorted(scores.items(), key=lambda x: -x[1])[:3]
        type_scores = "|".join(
            f"{t}:{int(s * 100)}" for t, s in sorted_scores if s > 0.05
        )

        garbage_pct = self._detect_garbage(text[:30000])
        boilerplate_pct = (
            max(0, 1 - unique_pct)
            if unique_pct < LINE_STATS["boilerplate_unique_threshold"]
            else 0
        )
        prose_quality = self._prose_quality(
            entropy, ic, alpha / n, special / n, avg_line
        )
        info_density = self._info_density(
            prose_quality, garbage_pct, boilerplate_pct, scores
        )

        verdict, action = self._verdict(
            info_density, garbage_pct, boilerplate_pct, primary
        )
        flags = self._detect_flags(text[:30000], entropy, ic, max_line, unique_pct)
        best_sample = self._extract_best_sample(text, scores)

        return TextDigest(
            chars=n,
            lines=line_count,
            entropy=round(entropy, 3),
            entropy_verdict=entropy_verdict,
            ic=round(ic, 4),
            ic_verdict=ic_verdict,
            alpha_pct=round(alpha / n, 3),
            digit_pct=round(digit / n, 3),
            space_pct=round(space / n, 3),
            special_pct=round(special / n, 3),
            avg_line_len=avg_line,
            max_line_len=max_line,
            unique_line_pct=round(unique_pct, 3),
            primary_type=primary,
            confidence=round(confidence, 3),
            type_scores=type_scores,
            info_density=round(info_density, 3),
            prose_quality=round(prose_quality, 3),
            garbage_pct=round(garbage_pct, 3),
            boilerplate_pct=round(boilerplate_pct, 3),
            verdict=verdict,
            action=action,
            flags=flags,
            best_sample=best_sample,
        )

    def _entropy(self, freq: Counter, n: int) -> float:
        if n == 0:
            return 0.0
        log_n = self._log_table.get(n, math.log2(n))
        h = 0.0
        for c in freq.values():
            p = c / n
            if p:
                log_c = self._log_table.get(c, math.log2(c))
                h -= p * (log_c - log_n)
        return h

    def _ic(self, freq: Counter) -> float:
        n = sum(freq.values())
        if n < 2:
            return 0.0
        num = sum(c * (c - 1) for c in freq.values())
        return num / (n * (n - 1))

    def _classify_entropy(self, h: float) -> str:
        if h < 3.8:
            return "compressed"
        if h <= 4.5:
            return "prose"
        if h <= 5.0:
            return "mixed"
        if h <= 5.4:
            return "markup"
        if h <= 5.8:
            return "minified"
        if h <= 6.1:
            return "encoded"
        return "noise"

    def _classify_ic(self, ic: float) -> str:
        if ic >= 0.062:
            return "english"
        if ic >= 0.048:
            return "code"
        if ic >= 0.040:
            return "mixed"
        return "random"

    def _score_types(
        self,
        sample: str,
        n: int,
        entropy: float,
        ic: float,
        alpha_r: float,
        special_r: float,
        html_r: float,
        code_r: float,
        md_r: float,
        data_r: float,
        avg_line: int,
        max_line: int,
        unique_pct: float,
    ) -> dict:
        scores = {
            "prose": 0.0,
            "code": 0.0,
            "html": 0.0,
            "markdown": 0.0,
            "data": 0.0,
            "noise": 0.0,
        }

        if ENTROPY["english_prose"][0] <= entropy <= ENTROPY["english_prose"][1]:
            scores["prose"] += 0.35
        elif ENTROPY["informal_text"][0] <= entropy <= ENTROPY["informal_text"][1]:
            scores["prose"] += 0.20

        if IC["english"][0] <= ic <= IC["english"][1]:
            scores["prose"] += 0.30
        elif IC["english_informal"][0] <= ic <= IC["english_informal"][1]:
            scores["prose"] += 0.15

        if alpha_r >= CHAR_RATIOS["prose"]["alpha"][0]:
            scores["prose"] += 0.20
        if special_r <= CHAR_RATIOS["prose"]["special"][1]:
            scores["prose"] += 0.15

        if ENTROPY["code_with_comments"][0] <= entropy <= ENTROPY["pure_code"][1]:
            scores["code"] += 0.25

        if IC["code_english_vars"][0] <= ic <= IC["code_english_vars"][1]:
            scores["code"] += 0.20
        elif IC["code_terse"][0] <= ic <= IC["code_terse"][1]:
            scores["code"] += 0.15

        if CHAR_RATIOS["code"]["special"][0] <= special_r <= CHAR_RATIOS["code"]["special"][1]:
            scores["code"] += 0.20

        code_patterns = [
            r"\b(def|function|class|const|let|var|import|return)\b",
            r"=>",
            r"\{\s*$",
            r";\s*$",
        ]
        for pattern in code_patterns:
            if re.search(pattern, sample, re.M):
                scores["code"] += 0.08

        indent_lines = [
            line for line in sample.split("\n")[:200] if line.startswith((" ", "\t"))
        ]
        if len(indent_lines) > 10:
            scores["code"] += 0.15

        if html_r > 0.08:
            scores["html"] += 0.30
        elif html_r > 0.04:
            scores["html"] += 0.15

        tag_count = len(re.findall(r"<[a-zA-Z][^>]*>", sample[:10000]))
        if tag_count > 20:
            scores["html"] += 0.35
        elif tag_count > 5:
            scores["html"] += 0.20

        if "<!DOCTYPE" in sample[:500] or "<html" in sample[:500].lower():
            scores["html"] += 0.30

        md_patterns = [
            (r"^#{1,6}\s+\S", 0.20),
            (r"^\s*[-*+]\s+\S", 0.12),
            (r"\[.+\]\(.+\)", 0.15),
            (r"^```", 0.18),
            (r"^\s*>\s+\S", 0.10),
            (r"\*\*[^*]+\*\*", 0.12),
            (r"__[^_]+__", 0.08),
            (r"\*[^*]+\*(?!\*)", 0.06),
            (r"^\|.+\|$", 0.10),
        ]
        for pattern, weight in md_patterns:
            if re.search(pattern, sample[:10000], re.M):
                scores["markdown"] += weight

        if "```" in sample:
            scores["markdown"] += 0.20
            scores["code"] = max(0, scores["code"] - 0.15)

        if data_r > 0.15:
            scores["data"] += 0.25

        if re.search(r"^\s*[\[{]", sample[:100]) and re.search(
            r"\"\w+\"\s*:", sample[:1000]
        ):
            scores["data"] += 0.40

        csv_lines = [line for line in sample.split("\n")[:20] if "," in line]
        if len(csv_lines) > 5:
            comma_counts = [line.count(",") for line in csv_lines]
            if len(set(comma_counts)) <= 2:
                scores["data"] += 0.35

        if entropy > ENTROPY["minified"][1]:
            scores["noise"] += 0.30
        if entropy > ENTROPY["base64"][0]:
            scores["noise"] += 0.30
        if ic < IC["random"][1]:
            scores["noise"] += 0.25

        if re.search(r"[A-Za-z0-9+/]{60,}={0,2}", sample):
            scores["noise"] += 0.25

        total = sum(scores.values())
        if total > 0:
            scores = {k: v / total for k, v in scores.items()}

        return scores

    def _detect_garbage(self, sample: str) -> float:
        if not sample:
            return 0.0
        garbage_chars = 0
        for pattern in self.GARBAGE_RE:
            for match in pattern.finditer(sample):
                garbage_chars += len(match.group())

        data_uris = re.findall(r"data:[^;]+;base64,[A-Za-z0-9+/=]+", sample)
        garbage_chars += sum(len(d) for d in data_uris)

        script_blocks = re.findall(r"<script[^>]*>.*?</script>", sample, re.S | re.I)
        garbage_chars += sum(len(s) * 0.7 for s in script_blocks)

        style_blocks = re.findall(r"<style[^>]*>.*?</style>", sample, re.S | re.I)
        garbage_chars += sum(len(s) * 0.5 for s in style_blocks)

        return min(1.0, garbage_chars / len(sample))

    def _prose_quality(
        self,
        entropy: float,
        ic: float,
        alpha_r: float,
        special_r: float,
        avg_line: int,
    ) -> float:
        score = 0.0

        if ENTROPY["english_prose"][0] <= entropy <= ENTROPY["english_prose"][1]:
            score += 0.30
        elif ENTROPY["informal_text"][0] <= entropy <= ENTROPY["informal_text"][1]:
            score += 0.20
        elif entropy > 5.0:
            score -= 0.10

        ic_dist = abs(ic - 0.067)
        if ic_dist < 0.005:
            score += 0.30
        elif ic_dist < 0.010:
            score += 0.20
        elif ic_dist < 0.020:
            score += 0.10

        if alpha_r >= 0.80:
            score += 0.20
        elif alpha_r >= 0.70:
            score += 0.10

        if special_r <= 0.03:
            score += 0.15
        elif special_r <= 0.06:
            score += 0.08

        if 40 <= avg_line <= 100:
            score += 0.10

        return min(1.0, max(0.0, score))

    def _info_density(
        self,
        prose_q: float,
        garbage: float,
        boilerplate: float,
        scores: dict,
    ) -> float:
        density = prose_q * 0.4

        useful_score = (
            scores.get("prose", 0)
            + scores.get("code", 0) * 0.8
            + scores.get("markdown", 0) * 0.9
            + scores.get("data", 0) * 0.6
        )
        density += useful_score * 0.3

        density -= garbage * 0.4
        density -= boilerplate * 0.3

        if scores.get("prose", 0) > 0.5 or scores.get("markdown", 0) > 0.5:
            density += 0.15

        return min(1.0, max(0.0, density))

    def _verdict(
        self,
        info_density: float,
        garbage: float,
        boilerplate: float,
        primary: str,
    ) -> tuple[str, str]:
        if primary == "noise" or garbage > 0.5:
            return "garbage", "skip"
        if info_density >= 0.70 and garbage < 0.1 and boilerplate < 0.1:
            return "pristine", "process"
        if info_density >= 0.50 and garbage < 0.2:
            return "clean", "process"
        if info_density >= 0.30 or (
            primary in ("prose", "markdown", "code") and garbage < 0.3
        ):
            return "usable", "clean_first"
        if info_density >= 0.15 or primary in ("prose", "markdown"):
            return "dirty", "extract_only"
        return "garbage", "skip"

    def _detect_flags(
        self,
        sample: str,
        entropy: float,
        ic: float,
        max_line: int,
        unique_pct: float,
    ) -> str:
        flags = []

        if re.search(r"[A-Za-z0-9+/]{40,}={0,2}", sample):
            flags.append("base64")

        if max_line > LINE_STATS["minified_line_len"]:
            flags.append("minified")

        if unique_pct < LINE_STATS["severe_boilerplate_threshold"]:
            flags.append("severe_boilerplate")
        elif unique_pct < LINE_STATS["boilerplate_unique_threshold"]:
            flags.append("boilerplate")

        if "\ufffd" in sample or re.search(r"\\x[0-9a-f]{2}", sample):
            flags.append("encoding_issues")

        if IC["multilingual"][0] <= ic <= IC["multilingual"][1] and entropy < 5.0:
            flags.append("multilingual")

        if "```" in sample or re.search(r"<pre[^>]*>|<code[^>]*>", sample):
            flags.append("has_code_blocks")

        if sample.count("<script") > 3:
            flags.append("script_heavy")

        if sample.count("|") > 20 or "<table" in sample:
            flags.append("has_tables")

        return ",".join(flags)

    def _extract_best_sample(self, text: str, scores: dict) -> str:
        lines = text.split("\n")
        best = ""
        best_score = -1.0
        paragraph = []

        for line in lines[:300]:
            stripped = line.strip()
            if not stripped:
                if paragraph:
                    candidate = " ".join(paragraph)
                    score = self._sample_score(candidate)
                    if score > best_score:
                        best_score = score
                        best = candidate
                    paragraph = []
                continue

            if stripped.startswith(("<", "{", "[", "//", "#!", "/*")):
                continue
            if len(stripped) < 20:
                continue
            paragraph.append(stripped)

        if paragraph:
            candidate = " ".join(paragraph)
            score = self._sample_score(candidate)
            if score > best_score:
                best = candidate

        if len(best) > 150:
            best = best[:147].rsplit(" ", 1)[0] + "..."

        return best

    def _sample_score(self, text: str) -> float:
        if len(text) < 30:
            return -1.0

        alpha_r = sum(1 for c in text if c.isalpha()) / len(text)
        special_r = sum(
            1 for c in text if not c.isalnum() and not c.isspace()
        ) / len(text)

        score = alpha_r - special_r * 2

        for pattern in self.QUALITY_RE:
            if pattern.search(text):
                score += 0.1

        score += min(0.2, len(text) / 500)
        return score

    def _empty(self) -> TextDigest:
        return TextDigest(
            chars=0,
            lines=0,
            entropy=0,
            entropy_verdict="empty",
            ic=0,
            ic_verdict="empty",
            alpha_pct=0,
            digit_pct=0,
            space_pct=0,
            special_pct=0,
            avg_line_len=0,
            max_line_len=0,
            unique_line_pct=0,
            primary_type="empty",
            confidence=0,
            type_scores="",
            info_density=0,
            prose_quality=0,
            garbage_pct=0,
            boilerplate_pct=0,
            verdict="garbage",
            action="skip",
            flags="empty",
            best_sample="",
        )


_digestor = Digestor()


def digest(text: str) -> TextDigest:
    return _digestor.digest(text)


def digest_to_prompt(text: str) -> str:
    return _digestor.digest(text).to_prompt()
