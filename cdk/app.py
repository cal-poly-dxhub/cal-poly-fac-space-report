#!/usr/bin/env python3
from pathlib import Path

import aws_cdk as cdk
import yaml

from fac_space_report.stack import FacSpaceReportStack

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
SETTINGS = {"require_mfa", "api_logging"}


def load_config() -> dict:
    """Read config.yaml, stopping the synth on anything it would otherwise guess at."""

    def fail(message):
        raise SystemExit(f"config.yaml: {message}")

    config = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    if not isinstance(config, dict):
        fail("expected settings as key: value lines")
    # A misspelt key would otherwise be ignored and its setting silently left at the default.
    if unknown := sorted(set(config) - SETTINGS):
        fail(f"unknown setting {', '.join(unknown)}. The settings are {', '.join(sorted(SETTINGS))}")

    for key in ("require_mfa", "api_logging"):
        config.setdefault(key, False)
        # A quoted "false" is a string, and a non-empty string is truthy.
        if not isinstance(config[key], bool):
            fail(f"{key} must be true or false, got {config[key]!r}")
    return config


config = load_config()
app = cdk.App()

FacSpaceReportStack(
    app,
    "FacSpaceReportStack",
    require_mfa=config["require_mfa"],
    api_logging=config["api_logging"],
    description="fac-space-report: CSU facility report, per docs/aws-deployment.drawio",
    # Stack tags: CloudFormation copies them onto every resource that supports tags.
    tags={"Project": "fac-space-report"},
)

app.synth()
