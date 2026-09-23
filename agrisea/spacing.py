"""회의록 본문 띄어쓰기 교정.

국회 회의록 PDF는 양쪽 정렬로 조판돼 있어 텍스트로 뽑으면 두 가지 오류가 생긴다.
  - 붙음: 자간이 좁은 줄에서 띄어쓰기가 사라짐  '저희들이간략하게정리를해드렸습니다'
  - 끊김: 줄 끝에서 낱말이 잘린 자리가 공백이 됨   '정 부가', '보고드리겠습니 다'
여기에 문장부호 뒤 공백 누락('습니다.그래서'), 목차 점선('······38') 같은 잔여물이 섞인다.

교정 기준은 사전이 아니라 회의록 말뭉치 자체다. 띄어쓰기가 정상인 문장에서 어절 빈도와
'앞 어절 + 뒤 어절' 빈도를 세어 두고
  - 끊김: 두 어절을 붙인 형태가 띄어 쓴 형태보다 훨씬 자주 쓰이면 붙인다
  - 붙음: 긴 한글 덩어리를 가장 그럴듯한 어절 열로 나눈다(어절 빈도 기반 최적 분할)
위원회 회의록은 같은 기관명·법률명·표현이 반복돼 말뭉치 빈도가 잘 맞는다.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable

# 교정 규칙이 바뀌면 올린다(저장된 발언을 다시 교정함).
SPACING_VERSION = 1

HANGUL_RUN = re.compile(r"[가-힣]+")
TOKEN_CORE = re.compile(r"^([\"'‘“(\[]*)([가-힣]+)([^가-힣]*)$")  # 앞 부호, 한글 본체, 뒤 부호
HEAD = re.compile(r"^[가-힣]+")   # 어절 앞쪽 한글
TAIL = re.compile(r"[가-힣]+$")   # 어절 뒤쪽 한글
HANGUL_REGION = re.compile(r"[가-힣]+(?: [가-힣]+)*")  # 공백 하나로 이어진 한글 구간
SENT_SPLIT = re.compile(r"(?<=[.?!])\s+")

# 문장부호 뒤 공백 누락: '습니다.그래서', '예,그렇습니다' (숫자 사이 '1,110'·'1.5'는 그대로)
PUNCT_NO_SPACE = re.compile(r"(?<=[가-힣a-zA-Z)])([.?!,])(?=[가-힣‘“(])|(?<=[가-힣])([.?!,])(?=\d)")
# 목차 점선과 쪽 번호: '(청원번호2200026)······················38'
LEADER_DOTS = re.compile(r"\s*[·…ㆍ.]{4,}\s*\d{0,4}")
# 조사로 끝난 한글 바로 뒤 숫자: '조합은1110개' → '조합은 1110개' ('제2조제1호'는 그대로)
PARTICLE_DIGIT = re.compile(r"(?<=[가-힣]{2})(?<=[은는이가을를와과도에])(?=\d)")

LONG_RUN = 8          # 이 길이 이상의 한글 덩어리만 분할 대상
LONG_RUN_DENSE = 5    # 띄어쓰기가 사라진 문장 안에서는 더 짧은 덩어리도 분할
BIGRAM_WEIGHT = 0.4   # 분할 시 앞 조각과 함께 쓰인 빈도의 가중치
MIN_WORD = 2          # 분할 조각으로 인정할 최소 빈도
JOIN_MIN = 3          # 붙인 형태의 최소 빈도
JOIN_RATIO = 4.0      # 붙인 형태 빈도 ≥ 띄어 쓴 형태 빈도 × 이 값이면 붙인다
UNKNOWN_PER_SYL = 4.5  # 모르는 조각의 음절당 비용(nats)
SPACE_CROSS = 4.0     # 원문 공백을 없애고 붙이는 비용(원문 띄어쓰기도 약한 근거로 쓴다)
NEW_SPACE = 1.0       # 원문에 없던 곳을 띄우는 비용
BOUND_ALONE = 8.0     # 조사·어미가 홀로 어절이 되는 비용
SINGLE_SYL = 3.0      # 한 음절 조각('운 전', '세 분 화')을 새로 만드는 비용

# 앞말에 붙어 쓰는 조사·어미·접미사: 홀로 어절이 되지 않는다('지역 에' → '지역에')
BOUND = frozenset("""은 는 가 을 를 에 에서 에게 께서 의 로 으로 와 과 도 만 들 들이 들은 들을 들의
    까지 부터 처럼 보다 보다는 마다 라든지 이라든지 이라는 라는 이라고 라고 이란 입니다 이고 이며 이나 적 적으로 적인 지 는지 요""".split())
# 낱말 첫머리에 오지 않는 음절: 어절이 이 음절로 시작하면 앞 어절의 조사가 끊겨 넘어온 것
NEVER_INITIAL = ("을", "를", "는")


def normalize_punct(text: str) -> str:
    text = LEADER_DOTS.sub(" ", text)
    text = PUNCT_NO_SPACE.sub(lambda m: (m.group(1) or m.group(2)) + " ", text)
    return PARTICLE_DIGIT.sub(" ", text)


def _clean_sentence(sent: str) -> bool:
    """띄어쓰기가 정상으로 보이는 문장: 긴 한글 덩어리가 없고 평균 어절 길이가 보통."""
    runs = HANGUL_RUN.findall(sent)
    if len(runs) < 4:
        return False
    if max(map(len, runs)) > 7:
        return False
    return sum(map(len, runs)) / len(runs) <= 4.2


def _core(tok: str) -> tuple[str, str, str] | None:
    m = TOKEN_CORE.match(tok)
    return (m.group(1), m.group(2), m.group(3)) if m else None


class SpacingModel:
    def __init__(self, words: Counter, pairs: Counter):
        self.words = words
        self.pairs = pairs
        self.total = max(1, sum(words.values()))
        self.prevs: dict[str, dict[str, int]] = {}   # 뒤 어절 → {앞 어절: 빈도}
        for (a, b), cnt in pairs.items():
            self.prevs.setdefault(b, {})[a] = cnt
        self._probs: dict[str, float] = {}

    @classmethod
    def train(cls, texts: Iterable[str]) -> "SpacingModel":
        """두 번 센다: 처음 센 빈도로 줄 끝에서 끊긴 조각('니다', '적으로')을 붙인 뒤 다시 세어
        끊긴 조각이 독립 어절로 학습되지 않게 한다."""
        clean = [sent for text in texts for sent in SENT_SPLIT.split(normalize_punct(text))
                 if _clean_sentence(sent)]
        first = cls._count(sent.split() for sent in clean)
        return cls._count(first._join(sent.split()) for sent in clean)

    @classmethod
    def _count(cls, sentences: Iterable[list[str]]) -> "SpacingModel":
        words: Counter = Counter()
        pairs: Counter = Counter()
        for toks in sentences:
            prev = None
            for tok in toks:
                c = _core(tok)
                if not c:
                    prev = None
                    continue
                words[c[1]] += 1
                if prev is not None:
                    pairs[(prev, c[1])] += 1
                # 뒤에 부호가 붙은 어절(문장 끝·쉼표) 다음은 이어진 어절로 보지 않는다
                prev = c[1] if not c[2] else None
        return cls(words, pairs)

    # ── 끊김: 두 어절 붙이기 ──
    def should_join(self, a: str, b: str) -> bool:
        joined = self.words.get(a + b, 0)
        if joined < JOIN_MIN:
            return False
        return joined >= JOIN_RATIO * (self.pairs.get((a, b), 0) + 1)

    def _join(self, toks: list[str]) -> list[str]:
        """앞 어절이 한글로 끝나고 뒤 어절이 한글로 시작할 때, 붙인 형태가 훨씬 흔하면 붙인다."""
        out: list[str] = []
        for tok in toks:
            if out:
                a, b = TAIL.search(out[-1]), HEAD.match(tok)
                if a and b:
                    # 비교는 뒤 어절의 한글 본체 전체로(부호 앞까지): '니다.' → '니다'
                    if self.should_join(a.group(), b.group()):
                        out[-1] = out[-1] + tok
                        continue
            out.append(tok)
        return out

    # ── 붙음: 긴 덩어리 나누기 ──
    def _prob(self, w: str) -> float:
        p = self._probs.get(w)
        if p is None:
            p = self._probs[w] = self._prob_uncached(w)
        return p

    def _prob_uncached(self, w: str) -> float:
        n = self.words.get(w, 0)
        if n >= MIN_WORD:
            return n / self.total
        # 처음 보는 어절이라도 '아는 낱말 + 조사'면 낱말 빈도로 가늠: '특별재난지역에'
        for k in (4, 3, 2, 1):
            if len(w) > k and w[-k:] in BOUND and self.words.get(w[:-k], 0) >= MIN_WORD:
                return 0.05 * self.words[w[:-k]] / self.total
        return math.exp(-UNKNOWN_PER_SYL * len(w)) / self.total

    def _bound(self, w: str) -> float:
        return BOUND_ALONE if (w in BOUND or w.startswith(NEVER_INITIAL)) else 0.0

    def segment(self, run: str, min_len: int = LONG_RUN,
                spaces: frozenset[int] = frozenset()) -> list[str]:
        """어절 빈도와 '앞 어절–뒤 어절' 빈도로 가장 그럴듯한 어절 열을 찾는다(비터비).
        spaces: 원문에서 공백이 있던 위치. 그 자리를 붙이려면 비용이 든다."""
        if not spaces and (len(run) < min_len or self.words.get(run, 0) >= MIN_WORD):
            return [run]
        n, span = len(run), 12
        cross = [0] * (n + 1)   # cross[j]-cross[i+1]: run[i:j] 안에 든 원문 공백 수
        for j in range(1, n + 1):
            cross[j] = cross[j - 1] + (0 < j - 1 and (j - 1) in spaces)
        # best[j][i]: run[:j]를 나누되 마지막 조각이 run[i:j]일 때 최소 비용
        best: list[dict[int, float]] = [dict() for _ in range(n + 1)]
        back: list[dict[int, int]] = [dict() for _ in range(n + 1)]
        top: list[tuple[float, int]] = [(math.inf, -1)] * (n + 1)  # j에서 끝나는 최소 비용과 그 i
        prevs = self.prevs
        for j in range(1, n + 1):
            for i in range(max(0, j - span), j):
                w = run[i:j]
                extra = SPACE_CROSS * (cross[j] - cross[i + 1])
                if j - i == 1 and not ((i == 0 or i in spaces) and (j == n or j in spaces)):
                    extra += SINGLE_SYL
                if i == 0:
                    best[j][0], back[j][0] = -math.log(self._prob(w)) + extra, -1
                else:
                    if not best[i]:
                        continue
                    extra += self._bound(w) + (NEW_SPACE if i not in spaces else 0.0)
                    pu = (1 - BIGRAM_WEIGHT) * self._prob(w)
                    cost, k = top[i][0] - math.log(pu), top[i][1]
                    # 앞 조각과 함께 쓰인 적이 있으면 그 경로를 따로 따져 본다
                    for prev, cnt in prevs.get(w, {}).items():
                        kk = i - len(prev)
                        if kk in best[i] and run[kk:i] == prev:
                            c = best[i][kk] - math.log(pu + BIGRAM_WEIGHT * cnt / self.words[prev])
                            if c < cost:
                                cost, k = c, kk
                    best[j][i], back[j][i] = cost + extra, k
                if best[j][i] < top[j][0]:
                    top[j] = (best[j][i], i)
        j, i = n, top[n][1]
        pieces = []
        while True:
            pieces.append(run[i:j])
            k = back[j][i]
            if k < 0:
                break
            j, i = i, k
        return pieces[::-1]

    def _resegment_region(self, region: str) -> str:
        """공백 섞인 한글 구간을 공백 없이 이어 붙인 뒤, 원문 공백을 약한 근거로 삼아 다시 나눈다."""
        spaces, chars = set(), []
        for ch in region:
            if ch == " ":
                spaces.add(len(chars))
            else:
                chars.append(ch)
        return " ".join(self.segment("".join(chars), LONG_RUN_DENSE, frozenset(spaces)))

    def correct(self, text: str) -> str:
        sents = []
        for sent in SENT_SPLIT.split(normalize_punct(text)):
            toks = sent.split()
            runs = [r for t in toks for r in HANGUL_RUN.findall(t)]
            dense = bool(runs) and (sum(map(len, runs)) / len(runs) > 4.5
                                    or max(map(len, runs)) >= 10)
            if dense:
                # 띄어쓰기가 무너진 문장: 줄바꿈 공백과 사라진 공백이 섞여 있어 구간째 다시 나눈다
                sent = HANGUL_REGION.sub(lambda m: self._resegment_region(m.group()), " ".join(toks))
                toks = sent.split()
            else:
                toks = self._join(toks)                # 끊긴 어절 붙이기
                toks = [HANGUL_RUN.sub(lambda m: " ".join(self.segment(m.group())), t)
                        for t in toks]                 # 긴 덩어리 나누기
            sents.append(" ".join(_attach_particles(" ".join(toks).split())))
        return " ".join(sents)


def _attach_particles(toks: list[str]) -> list[str]:
    """'어업 을하시는' → '어업을 하시는': 낱말 첫머리에 올 수 없는 조사를 앞 어절로 옮긴다."""
    out: list[str] = []
    for tok in toks:
        if out and TAIL.search(out[-1]):
            head = HEAD.match(tok)
            if head and head.group() in BOUND and head.end() == len(tok.rstrip(".,?!…")):
                out[-1] += tok                     # 조사만 홀로: '지역 에' → '지역에'
                continue
            if head and tok.startswith(NEVER_INITIAL) and len(head.group()) > 1:
                out[-1] += tok[0]
                tok = tok[1:]
        out.append(tok)
    return out


def correct_texts(texts: list[str]) -> list[str]:
    """말뭉치로 모델을 학습한 뒤 전체를 교정한다."""
    model = SpacingModel.train(texts)
    return [model.correct(t) for t in texts]
