"""
Cognito Pre Token Generation (V2) lambda source asset.

Packaged as a bundled CDK asset (see `infra/govcloud/cognito.py`).
The lambda runtime pulls ONLY this directory; no runtime deps on
the `forge` package or any third-party library — the handler is a
pure stdlib pass-through that reads user-pool attributes and emits
them as access + id token claims.  Keeping zero deps means the
lambda cold-start is fast and the Inspector scan surface is empty.
"""
