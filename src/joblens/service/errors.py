"""What can go wrong in a service call, in words a person can act on.

A script used to catch the openai SDK's exceptions, httpx's and the CV reader's
itself and print a sentence for each. A web request needs the same sentences
and one more thing, a status code, and it should not have to know that openai
or httpx exist. So a service call raises one of these and nothing else that a
caller is expected to handle: the message is already the sentence to show, and
the class says whose fault it is.
"""


class ServiceError(Exception):
    """Base class: str(error) is the message to show the person."""


class CVUnreadable(ServiceError):
    """The CV itself is the problem: a scan, a broken font, an empty profile.

    Nothing the person's provider did wrong, and trying again will not help;
    a different file will.
    """


class ProviderUnreachable(ServiceError):
    """No answer at all: a wrong base URL, no network, Ollama not running."""


class ProviderRefused(ServiceError):
    """The provider answered, and said no, after the client's own retries.

    `busy` is the one case where waiting helps (429 and 503: "high demand",
    measured on Gemini on 2026-09-24). Anything else -- a wrong key, a model
    that does not exist, a request it will not take -- needs a change first.
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code

    @property
    def busy(self) -> bool:
        return self.status_code in (429, 503)
