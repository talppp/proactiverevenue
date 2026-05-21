#!/usr/bin/env bash
# L1 SMS-phone smoke test — bash version of scripts/smoke_test.ps1.
# Runs the same 9 checks; exit 0 if all pass, 1 otherwise.

set -u

URL="${URL:-https://apteker-router-l1.onrender.com}"
SMS_TOKEN="${SMS_TOKEN:-TaYpMgFK3guXy2dU6nkRbpjy_j1IH5s1briRlUuwXLg}"
ADMIN_TOKEN="${ADMIN_TOKEN:-TYFesbM02AO0Ya-r2ql0M2VKcHWtMpyk6YhuAUhyKs4}"

passed=0
failed=0
results=()

record() {
    local name="$1" pass="$2" detail="${3:-}"
    if [ "$pass" = "1" ]; then
        printf "  \033[32m-> PASS\033[0m"
        passed=$((passed+1))
    else
        printf "  \033[31m-> FAIL\033[0m"
        failed=$((failed+1))
    fi
    [ -n "$detail" ] && printf "  (%s)" "$detail"
    echo
    results+=("$pass|$name|$detail")
}

pretty() { python3 -m json.tool 2>/dev/null || cat; }

echo "================================================================"
echo "  L1 SMS-phone deploy smoke test"
echo "  Target: $URL"
echo "  Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "================================================================"

echo
echo "[1/9] GET /healthz (public)"
body=$(curl -sS "$URL/healthz")
echo "$body" | pretty
echo "$body" | grep -q '"layer": "l1_sms_phone"' && record healthz 1 || record healthz 0

echo
echo "[2/9] POST /webhooks/sms_phone WITHOUT bearer (expect 401)"
status=$(curl -sS -o /tmp/smk2 -w "%{http_code}" -X POST "$URL/webhooks/sms_phone" \
    -H "Content-Type: application/json" -d '{}')
echo "  status=$status"
[ "$status" = "401" ] && record unauth-rejected 1 || record unauth-rejected 0 "got $status"

echo
echo "[3/9] POST /webhooks/sms_phone WITH wrong bearer (expect 401)"
status=$(curl -sS -o /tmp/smk3 -w "%{http_code}" -X POST "$URL/webhooks/sms_phone" \
    -H "Authorization: Bearer not-the-real-token" \
    -H "Content-Type: application/json" -d '{}')
echo "  status=$status"
[ "$status" = "401" ] && record wrong-bearer-rejected 1 || record wrong-bearer-rejected 0

echo
echo "[4/9] POST /webhooks/sms_phone (happy path)"
payload='{"from_number":"+14045551234","body":"render smoke test from script","received_at_phone":"2026-05-21T15:30:00+00:00"}'
body=$(curl -sS -X POST "$URL/webhooks/sms_phone" \
    -H "Authorization: Bearer $SMS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$payload")
echo "$body" | pretty
msg_id=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('msg_id',''))")
echo "$body" | grep -q '"duplicate": false' && [ ${#msg_id} -eq 64 ] \
    && record happy-path 1 "msg_id=${msg_id:0:16}..." \
    || record happy-path 0

echo
echo "[5/9] POST same payload AGAIN (expect duplicate=true)"
body=$(curl -sS -X POST "$URL/webhooks/sms_phone" \
    -H "Authorization: Bearer $SMS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$payload")
echo "$body" | pretty
echo "$body" | grep -q '"duplicate": true' && record idempotent 1 || record idempotent 0

echo
echo "[6/9] POST malformed body (expect 422)"
status=$(curl -sS -o /tmp/smk6 -w "%{http_code}" -X POST "$URL/webhooks/sms_phone" \
    -H "Authorization: Bearer $SMS_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"from_number":"+1","body":""}')
echo "  status=$status"
[ "$status" = "422" ] && record malformed-rejected 1 || record malformed-rejected 0 "got $status"

echo
echo "[7/9] GET /admin/sms_inbox_raw/health WITHOUT admin bearer (expect 401)"
status=$(curl -sS -o /tmp/smk7 -w "%{http_code}" "$URL/admin/sms_inbox_raw/health")
echo "  status=$status"
[ "$status" = "401" ] && record admin-unauth-rejected 1 || record admin-unauth-rejected 0

echo
echo "[8/9] GET /admin/sms_inbox_raw/health (with admin bearer)"
body=$(curl -sS "$URL/admin/sms_inbox_raw/health" \
    -H "Authorization: Bearer $ADMIN_TOKEN")
echo "$body" | pretty
handed_off=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('handed_off', -1))")
[ "$handed_off" -ge 1 ] 2>/dev/null \
    && record admin-health-counts 1 "handed_off=$handed_off" \
    || record admin-health-counts 0 "handed_off=$handed_off"

echo
echo "[9/9] POST /admin/sms_inbox_raw/inject (manual backfill)"
inject_body='{"from_number":"+14045559999","body":"admin backfill smoke test","received_at_phone":"2026-05-21T15:35:00+00:00"}'
body=$(curl -sS -X POST "$URL/admin/sms_inbox_raw/inject" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$inject_body")
echo "$body" | pretty
echo "$body" | grep -q '"status": "queued"' && record admin-inject 1 || record admin-inject 0

echo
echo "================================================================"
echo "  Summary: $passed/$((passed+failed)) passed"
for r in "${results[@]}"; do
    pass="${r%%|*}"; rest="${r#*|}"; name="${rest%%|*}"; detail="${rest#*|}"
    if [ "$pass" = "1" ]; then
        printf "  \033[32mPASS\033[0m  %s\n" "$name"
    else
        printf "  \033[31mFAIL\033[0m  %s  %s\n" "$name" "$detail"
    fi
done
echo "================================================================"

[ "$failed" -eq 0 ] || exit 1
