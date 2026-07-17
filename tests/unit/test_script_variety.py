from app.services.scripts.variety import (
    BRIDGE_STYLES,
    CORNER_STYLES,
    HOOK_STYLES,
    TSUKKOMI_DENSITIES,
    VarietyPlan,
    pick_variety_plan,
    variety_prompt_block,
)


def test_same_seed_and_template_returns_same_variety_plan() -> None:
    first = pick_variety_plan("topic-123", template="explainer")
    second = pick_variety_plan("topic-123", template="explainer")

    assert first == second


def test_several_seeds_produce_multiple_variations() -> None:
    plans = {
        (
            plan.hook_style,
            tuple(plan.corners),
            plan.tsukkomi_density,
            plan.bridge_style,
        )
        for index in range(12)
        for plan in [pick_variety_plan(f"topic-{index}", template="explainer")]
    }

    assert len(plans) >= 4


def test_news_commentary_never_selects_quiz() -> None:
    for index in range(50):
        plan = pick_variety_plan(f"news-{index}", template="news_commentary")
        assert "クイズ" not in plan.corners


def test_shorts_selects_at_most_one_corner() -> None:
    for index in range(50):
        plan = pick_variety_plan(f"short-{index}", template="shorts")
        assert len(plan.corners) <= 1


def test_unknown_template_fails_soft_with_valid_catalog_values() -> None:
    plan = pick_variety_plan("unknown-template", template="future_format")

    assert plan.hook_style in HOOK_STYLES
    assert set(plan.corners) <= set(CORNER_STYLES)


def test_prompt_block_is_nonempty_for_every_catalog_combination() -> None:
    for hook in HOOK_STYLES:
        for corner in CORNER_STYLES:
            for density in TSUKKOMI_DENSITIES:
                for bridge in BRIDGE_STYLES:
                    block = variety_prompt_block(
                        VarietyPlan(
                            hook_style=hook,
                            corners=[corner],
                            tsukkomi_density=density,
                            bridge_style=bridge,
                        )
                    )
                    assert block.strip()
                    assert hook in block
                    assert corner in block
