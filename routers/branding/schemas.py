import re
from typing import Dict, Optional

from pydantic import BaseModel, Field, field_validator

# Must match DEFAULT_VARIABLES in the app's src/features/Appearance/themes.ts.
COLOR_VARIABLES = frozenset({
    "--background", "--foreground", "--card", "--card-foreground", "--popover", "--popover-foreground",
    "--primary", "--primary-foreground", "--secondary", "--secondary-foreground", "--muted",
    "--muted-foreground", "--accent", "--accent-foreground", "--destructive", "--destructive-foreground",
    "--border", "--input", "--ring", "--nav", "--nav-foreground", "--nav-active", "--nav-active-foreground",
    "--header-from", "--header-via", "--header-to", "--header-border", "--page",
})
LENGTH_VARIABLES = frozenset({"--radius"})

# "h s% l%" with an optional "/ alpha" - nothing else can reach the generated stylesheet.
_NUMBER = r"\d{1,3}(?:\.\d{1,3})?"
_HSL_PATTERN = re.compile(rf"^{_NUMBER} {_NUMBER}% {_NUMBER}%(?: / (?:0|1|0?\.\d{{1,3}}|{_NUMBER}%))?$")
_LENGTH_PATTERN = re.compile(r"^\d{1,2}(?:\.\d{1,3})?(?:rem|px)$")
_THEME_ID_PATTERN = re.compile(r"^[a-z0-9-]{1,40}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class BrandingResponse(BaseModel):
    theme_id: str
    variables: Dict[str, str]
    app_title: str
    logo_version: Optional[str] = None
    favicon_version: Optional[str] = None


class BrandingUpdateRequest(BaseModel):
    theme_id: str = Field(max_length=40)
    variables: Dict[str, str] = Field(default_factory=dict)
    app_title: str = Field(default="", max_length=80)

    @field_validator("theme_id")
    @classmethod
    def _check_theme_id(cls, value: str) -> str:
        if not _THEME_ID_PATTERN.match(value):
            raise ValueError("theme_id may only contain lowercase letters, digits and dashes")
        return value

    @field_validator("variables")
    @classmethod
    def _check_variables(cls, value: Dict[str, str]) -> Dict[str, str]:
        cleaned: Dict[str, str] = {}
        for name, raw in value.items():
            css_value = raw.strip()
            if name in COLOR_VARIABLES:
                valid = _HSL_PATTERN.match(css_value)
            elif name in LENGTH_VARIABLES:
                valid = _LENGTH_PATTERN.match(css_value)
            else:
                raise ValueError(f"unknown theme variable '{name}'")
            if not valid:
                raise ValueError(f"invalid value for '{name}': '{raw}'")
            cleaned[name] = css_value
        return cleaned

    @field_validator("app_title")
    @classmethod
    def _check_app_title(cls, value: str) -> str:
        if _CONTROL_CHARS.search(value):
            raise ValueError("app_title must not contain control characters")
        return value.strip()
