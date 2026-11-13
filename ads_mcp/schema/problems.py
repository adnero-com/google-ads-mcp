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

"""What the body checks report (pure): a problem at a JSON pointer, and schema-walking helpers."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Problem:
    path: str  # JSON pointer into the body, e.g. /operations/0/create/appCampaignSetting/appStore
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"path": self.path, "code": self.code, "message": self.message}


def pointer(parts) -> str:
    """RFC 6901: '~' -> '~0', '/' -> '~1'."""
    return "".join(
        "/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts
    )


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def resolve(schema: Mapping, defs: Mapping) -> Mapping:
    """A `$ref` schema -> its definition (siblings like description dropped); anything else as is."""
    ref = schema.get("$ref", "") if isinstance(schema, Mapping) else ""
    if ref.startswith("#/$defs/"):
        return defs.get(ref.rsplit("/", 1)[-1], {})
    return schema
