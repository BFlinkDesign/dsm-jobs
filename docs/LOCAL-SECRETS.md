# Local Secrets

Runtime keys stay outside the repo. Do not paste values into chat, markdown,
GitHub secrets, or tracked files.

## Default Drop Paths

Create these files under `%USERPROFILE%\Secrets\dsm-jobs\`:

```text
edge-voice.env
supabase-admin.env
```

`edge-voice.env` is for Rudy voice provider secrets:

```dotenv
REPLICATE_API_TOKEN=replace-with-token
VOICE_TTS=chatterbox
CHATTERBOX_MODEL=resemble-ai/chatterbox
```

`supabase-admin.env` is for production snapshot/deploy verification:

```dotenv
SUPABASE_URL=https://tcclohxvhmwgjrtdkkuw.supabase.co
SUPABASE_SERVICE_KEY=replace-with-service-role-key
SUPABASE_ACCESS_TOKEN=replace-with-management-api-token
# Alternative full schema verifier if Management API is unavailable:
# SUPABASE_DB_PASSWORD=replace-with-db-password
# SUPABASE_POOLER_HOST=replace-with-verified-pooler-host
```

The scripts auto-load `supabase-admin.env` from that folder. To override paths
for one terminal session:

```powershell
$env:DSM_JOBS_SECRETS_DIR="$env:USERPROFILE\Secrets\dsm-jobs"
$env:DSM_JOBS_VOICE_ENV_FILE="$env:USERPROFILE\Secrets\dsm-jobs\edge-voice.env"
$env:DSM_JOBS_SUPABASE_ENV_FILE="$env:USERPROFILE\Secrets\dsm-jobs\supabase-admin.env"
```

## Safe Readiness Commands

These commands print key names and gate status only, never secret values:

```powershell
python scripts/verify_voice_readiness.py
python scripts/snapshot_supabase.py
python scripts/verify_supabase_schema.py --require-full
```

Only after those pass:

```powershell
supabase secrets set --env-file "$env:USERPROFILE\Secrets\dsm-jobs\edge-voice.env" --project-ref tcclohxvhmwgjrtdkkuw
supabase secrets list --project-ref tcclohxvhmwgjrtdkkuw
supabase functions deploy voice --project-ref tcclohxvhmwgjrtdkkuw
```

If `supabase secrets list` says an access token is missing, add the Management
API token to `supabase-admin.env` and export it into the terminal environment
for the CLI process. Do not commit it.
