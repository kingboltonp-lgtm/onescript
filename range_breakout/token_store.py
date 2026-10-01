"""Read and write the daily Upstox access token.

Upstox tokens expire every day around 03:30 IST, so a fresh one is generated
each morning (see get_token.py) and stored here. On AWS the token lives in SSM
Parameter Store as a SecureString so the bot and the login helper can run on
different machines.
"""
import os


def load_token(cfg):
    if cfg.token_source == "env":
        return os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if cfg.token_source == "file":
        try:
            with open(cfg.token_file) as fh:
                return fh.read().strip()
        except FileNotFoundError:
            return ""
    if cfg.token_source == "ssm":
        import boto3

        ssm = boto3.client("ssm", region_name=cfg.aws_region)
        try:
            resp = ssm.get_parameter(Name=cfg.token_ssm_param, WithDecryption=True)
        except ssm.exceptions.ParameterNotFound:
            return ""
        return resp["Parameter"]["Value"].strip()
    raise ValueError(f"Unknown TOKEN_SOURCE: {cfg.token_source}")


def save_token(cfg, token):
    if cfg.token_source == "file":
        with open(cfg.token_file, "w") as fh:
            fh.write(token)
        os.chmod(cfg.token_file, 0o600)
        return
    if cfg.token_source == "ssm":
        import boto3

        boto3.client("ssm", region_name=cfg.aws_region).put_parameter(
            Name=cfg.token_ssm_param, Value=token, Type="SecureString", Overwrite=True
        )
        return
    raise ValueError("TOKEN_SOURCE=env is read-only; set UPSTOX_ACCESS_TOKEN yourself")
