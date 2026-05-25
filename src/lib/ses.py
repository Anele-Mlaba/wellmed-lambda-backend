"""SES templated-email helpers. Fail-soft: callers log + ignore non-fatal errors."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

_LOG = logging.getLogger(__name__)

_FROM = os.environ["SES_FROM_ADDRESS"]
_TEMPLATE_CONFIRMATION = os.environ["SES_TEMPLATE_CONFIRMATION"]
_TEMPLATE_REMINDER = os.environ["SES_TEMPLATE_REMINDER"]

_client = boto3.client("ses")


def send_templated(template_name: str, to_address: str, data: dict[str, Any]) -> bool:
    try:
        _client.send_templated_email(
            Source=_FROM,
            Destination={"ToAddresses": [to_address]},
            Template=template_name,
            TemplateData=json.dumps(data),
        )
        return True
    except (BotoCoreError, ClientError):
        _LOG.exception("ses send failed: template=%s to=%s", template_name, to_address)
        return False


def send_booking_confirmation(to_address: str, data: dict[str, Any]) -> bool:
    return send_templated(_TEMPLATE_CONFIRMATION, to_address, data)


def send_booking_reminder(to_address: str, data: dict[str, Any]) -> bool:
    return send_templated(_TEMPLATE_REMINDER, to_address, data)
