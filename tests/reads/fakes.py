"""Fake Google Ads rows: nested SimpleNamespaces shaped like proto-plus messages (enums have .name)."""

from types import SimpleNamespace


def E(name):
    """An enum value."""
    return SimpleNamespace(name=name)


def obj(value):
    """dicts -> SimpleNamespace, recursively (lists kept as lists)."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: obj(v) for k, v in value.items()})
    if isinstance(value, list):
        return [obj(v) for v in value]
    return value


class GoogleError(Exception):
    """A GoogleAdsException look-alike: .failure.errors[].error_code.<kind> = enum, .message."""

    def __init__(self, kind, code, message="refused"):
        super().__init__(message)
        self.failure = SimpleNamespace(
            errors=[
                SimpleNamespace(
                    error_code=SimpleNamespace(**{kind: E(code)}),
                    message=message,
                )
            ]
        )


class FakeSearch:
    """GoogleAdsService.search: answers by the FROM resource; records every query."""

    def __init__(self, answers):
        self.answers, self.queries = answers, []

    def __call__(self, customer_id, query):
        self.queries.append(query)
        resource = query.split(" FROM ", 1)[1].split()[0]
        return self.answers.get(resource, [])
