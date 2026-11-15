"""설정 로딩: config/*.yaml + .env (API 키). 키는 repr/로그에 노출하지 않는다."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, SecretStr

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


class TourApiCfg(BaseModel):
    base_url: str = "https://apis.data.go.kr/B551011/KorService2"
    timeout_s: float = 10
    mobile_app: str = "eodigaji"
    content_type_id: int = 12


class SourceCfg(BaseModel):
    bulk_page_size: int = 5000
    refresh_hours: float = 24


class CacheCfg(BaseModel):
    dir: str = "data/cache"
    detail_ttl_days: float = 7
    ref_ttl_days: float = 30


class SpeedSegment(BaseModel):
    until_min: Optional[float] = None
    kmh: float


class RadiusCfg(BaseModel):
    detour_factor: float = 1.3
    min_radius_km: float = 3.0
    speed_segments: list[SpeedSegment] = Field(
        default_factory=lambda: [SpeedSegment(until_min=30, kmh=30), SpeedSegment(until_min=None, kmh=60)]
    )


class RetryCfg(BaseModel):
    """재추천 속도 보정: 속도 배율 = 서버 추정 운전시간 / 실제 ETA (요청 단위, 저장하지 않음)."""
    min_speed_factor: float = 0.3
    max_speed_factor: float = 2.0
    min_estimated_seconds: float = 120   # 직전 후보의 추정 운전시간이 이보다 짧으면 보정하지 않음 (잡음)


class EtaCheckCfg(BaseModel):
    tolerance_seconds: int = 0       # ETA <= drive_minutes*60 + 이 값이면 accept
    max_attempts: int = 3            # 추천 최대 횟수 (첫 추천 포함)


class DriveCfg(BaseModel):
    min: int = 15
    max: int = 120


class CandidateCfg(BaseModel):
    max_candidates: int = 20
    min_for_keyword: int = 3
    finalists: int = 3
    max_detail_lookups: int = 6
    exclude_lcls_prefixes: list[str] = Field(default_factory=list)


class ConditionsCfg(BaseModel):
    """추천 조건(require_parking, exclude_paid)."""
    max_lookups: int = 10          # 조건을 켰을 때 요청당 상세조회 상한
    batch_size: int = 5            # 한 번에 병렬로 조회할 곳 수
    parking_pass_rate: float = 0.7 # /keywords 추정: 주차 정보가 캐시에 없을 때 '가능'일 확률
    fee_pass_rate: float = 0.95    # /keywords 추정: 요금 정보가 캐시에 없을 때 '유료가 아닐' 확률


class ScoringCfg(BaseModel):
    kw_class: float = 1.0
    kw_text: float = 0.5
    time_base: float = 0.5
    time_text: float = 0.5
    multi_bonus: float = 0.3
    w_keyword: float = 1.0
    band_low: float = 0.6
    band_high: float = 1.0
    near_floor: float = 0.3
    w_distance: float = 1.0
    info_image: float = 0.25
    info_overview: float = 0.25
    closed_at_arrival_penalty: float = 0.5
    min_weight: float = 0.05


class TimeWindowCfg(BaseModel):
    sunrise_before_min: int = 30
    sunrise_after_min: int = 0
    sunset_before_min: int = 60
    sunset_after_min: int = 20
    night_after_sunset_min: int = 30
    night_until: str = "23:00"


class SeasonRule(BaseModel):
    months: list[int]
    keywords: list[str]
    text: list[str]
    bonus: float


class AliasCfg(BaseModel):
    min_len: int = 5
    max_len: int = 20
    recent_size: int = 10            # 최근에 고른 가명을 피할 개수 (메모리에만)
    # config/alias.yaml
    extra_forbidden: list[str] = Field(default_factory=list)
    allow_words: list[str] = Field(default_factory=list)
    # config/alias_pool.yaml
    pool: list[str] = Field(default_factory=list)


class Landmass(BaseModel):
    """다리로 육지와 이어지지 않은 땅. 출발지와 같은 땅의 후보만 남긴다 (첫 번째로 맞는 그룹)."""
    name: str
    regn: list[str] = Field(default_factory=list)          # 법정동 시도코드
    regn_signgu: list[str] = Field(default_factory=list)   # "시도_시군구"
    addr_contains: list[str] = Field(default_factory=list) # 주소에 이 중 하나가 있어야 함
    addr_exclude: list[str] = Field(default_factory=list)  # 다리로 이어진 곳 (예: 영흥면)


class BoundsCfg(BaseModel):
    lat_min: float = 33.0
    lat_max: float = 38.7
    lng_min: float = 124.5
    lng_max: float = 131.9


class KeywordDef(BaseModel):
    id: str
    label: str
    lcls: list[str] = Field(default_factory=list)
    title: list[str] = Field(default_factory=list)
    inland_exclude_lcls: list[str] = Field(default_factory=list)
    inland_only_lcls: list[str] = Field(default_factory=list)
    time_window: Optional[Literal["sunrise", "sunset", "night"]] = None
    base: list[str] = Field(default_factory=list)
    extra_lcls: list[str] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)


class KeywordsCfg(BaseModel):
    coastal_lcls: list[str] = Field(default_factory=list)   # 섬의 바다/물가 판정용 해안 분류
    keywords: dict[str, KeywordDef]

    @property
    def ids(self) -> list[str]:
        return list(self.keywords)


class Settings(BaseModel):
    timezone: str = "Asia/Seoul"
    random_seed: Optional[int] = None
    log_level: str = "INFO"
    landmasses: list[Landmass] = Field(default_factory=list)
    origin_landmass_max_km: float = 15.0
    tour_api: TourApiCfg = Field(default_factory=TourApiCfg)
    source: SourceCfg = Field(default_factory=SourceCfg)
    cache: CacheCfg = Field(default_factory=CacheCfg)
    radius: RadiusCfg = Field(default_factory=RadiusCfg)
    drive_minutes: DriveCfg = Field(default_factory=DriveCfg)
    retry: RetryCfg = Field(default_factory=RetryCfg)
    eta_check: EtaCheckCfg = Field(default_factory=EtaCheckCfg)
    candidates: CandidateCfg = Field(default_factory=CandidateCfg)
    conditions: ConditionsCfg = Field(default_factory=ConditionsCfg)
    scoring: ScoringCfg = Field(default_factory=ScoringCfg)
    time_windows: TimeWindowCfg = Field(default_factory=TimeWindowCfg)
    season_rules: list[SeasonRule] = Field(default_factory=list)
    alias: AliasCfg = Field(default_factory=AliasCfg)
    bounds: BoundsCfg = Field(default_factory=BoundsCfg)
    keywords: KeywordsCfg
    tour_kor_key: SecretStr = SecretStr("")
    # 앱 → 서버 API 키 (X-API-Key). 비어 있으면 검사하지 않는다. 여러 개는 키 교체용
    app_api_keys: list[SecretStr] = Field(default_factory=list)

    @property
    def cache_dir(self) -> Path:
        p = Path(self.cache.dir)
        return p if p.is_absolute() else ROOT / p


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_settings(config_dir: Path = CONFIG_DIR, env_file: Optional[Path] = ROOT / ".env") -> Settings:
    raw = _read_yaml(config_dir / "settings.yaml")
    kw_raw = _read_yaml(config_dir / "keywords.yaml")
    for kid, d in (kw_raw.get("keywords") or {}).items():
        d.setdefault("id", kid)
    raw["keywords"] = kw_raw
    alias = raw.setdefault("alias", {})
    alias.update(_read_yaml(config_dir / "alias.yaml"))
    alias["pool"] = (_read_yaml(config_dir / "alias_pool.yaml").get("names") or [])
    env = read_env_file(env_file) if env_file else {}
    raw["tour_kor_key"] = os.environ.get("TOURAPI_KOR_KEY") or env.get("TOURAPI_KOR_KEY", "")
    raw["app_api_keys"] = parse_api_keys(os.environ.get("APP_API_KEYS") or env.get("APP_API_KEYS", ""))
    return Settings.model_validate(raw)


def parse_api_keys(value: str) -> list[SecretStr]:
    """쉼표로 나눈 키 목록. 앞뒤 공백·빈 항목은 버린다."""
    return [SecretStr(k.strip()) for k in (value or "").split(",") if k.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
