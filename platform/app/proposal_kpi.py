"""申报书考核指标。新计算记录写入 VERSION；旧记录保持原指标版本判定，需要按新口径时重新计算。"""

VERSION = 'proposal-2026-11-v1'
LEGACY_VERSION = 'legacy-2026-10'

CURRENT = dict(
    version=VERSION,
    source='申报/latex申报书/sections/04a_objectives_kpi.tex',
    modalities=3,
    wide_cell_m=30,
    focus_cell_m=10,
    coverage=0.80,
    focus_coverage=0.85,
    platform_devices=3,
    planning_parameters=3,
    interference_factors=2,
    efficacy_metrics=2,
    demo_scenes=3,
    demo_tasks=3,
    papers=2,
    patents=3,
    software=2,
    graduate_students=2,
    engineers=4,
)

HISTORICAL = dict(
    version=LEGACY_VERSION,
    label='历史对照',
    modalities=3,
    wide_cell_m=10,
    focus_cell_m=2,
    coverage=0.90,
    focus_coverage=0.95,
    platform_devices=5,
    planning_parameters=4,
    interference_factors=3,
    efficacy_metrics=3,
    demo_scenes=3,
    demo_tasks=5,
    papers=5,
    patents=10,
    software=3,
    graduate_students=6,
    engineers=10,
    decides_pass=False,
)


def published_indicators():
    return dict(current=CURRENT, historical=HISTORICAL,
                rule='只按 current 判定；historical 仅作对照，不决定是否达标。')


def standard_for(version):
    return CURRENT if version == VERSION else HISTORICAL


def meets(value, threshold):
    return value is not None and value + 1e-12 >= threshold


def resolution_meets(wide_cell, focus_cell, standard):
    return wide_cell <= standard['wide_cell_m'] + 1e-9 and focus_cell <= standard['focus_cell_m'] + 1e-9


def simulation_checks(stats, wide_cell, focus_cell, modality_count):
    historical = dict(
        resolution=resolution_meets(wide_cell, focus_cell, HISTORICAL),
        coverage=meets(stats.get('coverage'), HISTORICAL['coverage']),
        focus_coverage=meets(stats.get('focus_coverage'), HISTORICAL['focus_coverage']),
        decides_pass=False,
    )
    return dict(
        resolution=resolution_meets(wide_cell, focus_cell, CURRENT),
        coverage=meets(stats.get('coverage'), CURRENT['coverage']),
        focus_coverage=meets(stats.get('focus_coverage'), CURRENT['focus_coverage']),
        modalities_3=modality_count >= CURRENT['modalities'],
        historical=historical,
        coverage_90=historical['coverage'],
        focus_95=historical['focus_coverage'],
    )


def recorded_checks(result):
    """按记录自身的指标版本取判定；旧记录沿用当时保存的 90% / 95% 与 10 m / 2 m 判定。"""
    checks = result.get('checks') or {}
    if result.get('kpi_version') == VERSION:
        return VERSION, CURRENT, checks
    legacy = dict(checks, coverage=checks.get('coverage_90', False), focus_coverage=checks.get('focus_95', False))
    return LEGACY_VERSION, HISTORICAL, legacy
