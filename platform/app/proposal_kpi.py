"""申报书考核指标。新计算记录写入 VERSION；旧记录保持原指标版本判定，需要按新口径时重新计算。"""

VERSION = 'proposal-2026-11-v2'
DRAFT_VERSION = 'proposal-2026-11-v1'
LEGACY_VERSION = 'legacy-2026-10'

CURRENT = dict(
    version=VERSION,
    source='正式申请202611/雄安新区科技创新专项储备项目申报书end-1124.doc',
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
)

HISTORICAL = dict(
    version=DRAFT_VERSION,
    label='早期稿对照',
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
    decides_pass=False,
)

LEGACY = dict(CURRENT, version=LEGACY_VERSION, source='平台早期验收口径')
DRAFT = {key: value for key, value in HISTORICAL.items() if key not in ('label', 'decides_pass')}


def published_indicators():
    return dict(current=CURRENT, historical=HISTORICAL,
                rule='只按 current 判定；historical 仅作对照，不决定是否达标。')


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
        coverage_90=meets(stats.get('coverage'), 0.90),
        focus_95=meets(stats.get('focus_coverage'), 0.95),
    )


def recorded_checks(result):
    """按记录自身的指标版本取判定；不把旧记录改按新口径解释。"""
    checks = result.get('checks') or {}
    version = result.get('kpi_version')
    if version == VERSION:
        return VERSION, CURRENT, checks
    if version == DRAFT_VERSION:
        return DRAFT_VERSION, DRAFT, checks
    legacy = dict(checks, coverage=checks.get('coverage_90', False), focus_coverage=checks.get('focus_95', False))
    return LEGACY_VERSION, LEGACY, legacy
