# Copyright 2026 Adnero.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The read tools' one door to Google errors (the edge): a GoogleAdsException becomes the structured error of
utils.format_google_ads_error (codes, field paths, triggers, fixes), never the raw gRPC exception text.
"""

from contextlib import contextmanager

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException

import ads_mcp.utils as utils


@contextmanager
def refusals():
    try:
        yield
    except GoogleAdsException as exc:
        raise ToolError(utils.format_google_ads_error(exc)) from None


def message(exc: Exception) -> str:
    """Text for a caught exception: the structured form for Google's, else its own words."""
    if isinstance(exc, GoogleAdsException):
        return utils.format_google_ads_error(exc)
    return str(exc)[:300] or type(exc).__name__
