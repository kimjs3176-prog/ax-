from agrisea.spacing import SpacingModel, normalize_punct

# 띄어쓰기가 정상인 문장(학습용). 실제로는 회의록 전체 발언으로 학습한다.
CLEAN = [
    "저희들이 간략하게 정리를 해 드렸습니다.",
    "그래서 그거에 대해서 간략하게 보고를 드리겠습니다.",
    "정부가 이 문제를 알고 있습니다.",
    "정부가 대책을 마련해야 합니다.",
    "정부가 예산을 편성했습니다.",
    "어업을 하시는 분들이 많습니다.",
    "그 부분은 정부가 검토하겠습니다.",
    "위원님 말씀대로 정부가 준비하겠습니다.",
    "할 수 있는 일을 하겠습니다.",
    "할 수 없는 일도 있습니다.",
]
MODEL = SpacingModel.train(CLEAN * 3)


def test_punctuation_and_leaders():
    assert normalize_punct("예,그렇습니다.그래서 보고") == "예, 그렇습니다. 그래서 보고"
    assert normalize_punct("조합은1110개, 1,110명, 1.5배") == "조합은 1110개, 1,110명, 1.5배"
    assert normalize_punct("제2조제1호") == "제2조제1호"
    assert "··" not in normalize_punct("(청원번호2200026)··························38 다음")


def test_joins_word_split_at_line_end():
    assert MODEL.correct("그 부분은 정 부가 검토하겠습니다.") == "그 부분은 정부가 검토하겠습니다."
    # 띄어 쓰는 것이 흔한 표현은 붙이지 않는다
    assert MODEL.correct("할 수 있는 일을 하겠습니다.") == "할 수 있는 일을 하겠습니다."


def test_splits_run_together_text():
    out = MODEL.correct("저희들이간략하게정리를해드렸습니다.그래서그거에대해서간략하게보고를드리겠습니다.")
    assert out == ("저희들이 간략하게 정리를 해 드렸습니다. "
                   "그래서 그거에 대해서 간략하게 보고를 드리겠습니다.")


def test_particle_moves_to_previous_word():
    assert MODEL.correct("그 어업 을하시는 분들이 많습니다.") == "그 어업을 하시는 분들이 많습니다."


def test_clean_text_unchanged():
    for s in CLEAN:
        assert MODEL.correct(s) == s


def test_precompute_fixes_spacing_once(store):
    from agrisea.precompute import fix_spacing
    with store.tx() as c:
        c.execute("UPDATE utterances SET text='정부가대책을마련해야합니다.' WHERE idx=0")
    assert fix_spacing(store, progress=lambda _: None) >= 0
    assert store.conn.execute("SELECT COUNT(*) FROM utterances WHERE spacing=0").fetchone()[0] == 0
    # 교정된 발언은 다시 고치지 않는다
    assert fix_spacing(store, progress=lambda _: None) == 0
