# PayConnect Log Extractor

Pulls full request/response blocks from MinIO log exports for one or more Pay IDs. Outputs one `.txt` per Pay ID.

## Setup

```bash
pip install -r requirements.txt
cp config.example.json config.json
```

Fill in `config.json`:

```json
{
  "endpoint": "https://sample.net",
  "username": "your-console-username",
  "password": "your-console-password",
  "bucket": "payconnect-log-exports-prod",
  "base_prefix": "logs/",
  "extensions": ".log,.gz,.txt",
  "block_marker": "Logging Request",
  "workers": 4,
  "out_dir": ""
}
```

## Run

```bash
python payid_log_extractor.py
```

GUI prompts for date range (optional), Pay ID(s) (comma-separated or one per line), and output folder. Zero-match Pay IDs are logged, no empty file written.

## How it works

- Auth: same login as MinIO console web UI
- Lists objects under `base_prefix`, paginated
- Downloads + decompresses (`.gz`) in parallel, concurrency = `workers`
- Splits log lines into blocks on `block_marker`, matches Pay ID against each block, writes hits

## Notes

- `block_marker` is logger-format-dependent — adjust per service if blocks aren't splitting correctly
- Tune `workers` up/down based on connection stability (timeouts = lower it)
