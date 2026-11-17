import ipaddress
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    type: Literal["RADAR", "RF", "EO", "IR", "ADSB"]
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)
    z: float = Field(ge=1, le=300)
    azimuth: float = Field(ge=0, le=360)
    pitch: float = Field(default=10,ge=-90,le=90)
    vertical_fov: float = Field(default=110,gt=0,le=180)
    range: float = Field(gt=0, le=20000)
    fov: float = Field(gt=0, le=360)
    cost: float = Field(ge=0, le=10000)
    status: Literal["online", "offline"] = "online"


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["ollama", "compatible"] = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = Field(default="qwen3:8b", max_length=120)
    temperature: float = Field(default=.2, ge=0, le=1)

    @field_validator("base_url")
    @classmethod
    def local_address(cls, value):
        url = urlsplit(value.strip())
        if url.scheme not in {"http","https"} or url.username or url.password or url.query or url.fragment:
            raise ValueError("请填写本机模型服务的 HTTP 地址")
        host = url.hostname
        if host != "localhost":
            try:
                if not ipaddress.ip_address(host or "").is_loopback:
                    raise ValueError()
            except ValueError:
                raise ValueError("基础版仅连接本机回环地址，项目资料不会发送到远程模型")
        if url.path.rstrip("/") not in {"", "/v1"}:
            raise ValueError("模型服务地址仅支持根路径或 /v1")
        return value.strip().rstrip("/")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1,max_length=4000)
    scene: Literal["urban", "corridor", "electromagnetic"] = "urban"
    session: str = Field(default="default", min_length=1,max_length=80,pattern=r"^[a-zA-Z0-9_-]+$")
    evidence_only: bool = False


class PlanRequest(BaseModel):
    budget: float = Field(default=50,ge=0,le=1000,allow_inf_nan=False)
    target: float = Field(default=.9,gt=0,le=1,allow_inf_nan=False)
    scene: Literal["urban", "corridor", "electromagnetic"] = "urban"
    objective: Literal["coverage", "task"] = "task"
