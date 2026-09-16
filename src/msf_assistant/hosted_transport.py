"""Requests options that keep hosted responses unconsumed until bounded ingestion."""

import requests


def reject_redirect(response: requests.Response, *args, **kwargs) -> requests.Response:
    """Run before Requests prepares Response.next or follows a redirect.

    Even allow_redirects=False normally consumes redirect bodies while preparing
    Response.next. A response hook runs earlier in Session.send and closes the
    unread response before that path can execute.
    """
    if 300 <= response.status_code < 400:
        response.close()
        raise requests.RequestException("Upstream redirect rejected")
    return response


def bounded_request_options() -> dict:
    """Fresh options for hosted calls only; local Requests behavior is unchanged."""
    return {"stream": True, "allow_redirects": False, "hooks": {"response": [reject_redirect]}}
