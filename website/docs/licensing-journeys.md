# Licensing Journeys and Edge Cases

## Core Principles
- Pricing enforcement is character-based (`quotaChars` + `bonusChars` - `usedChars`).
- Usage analytics are tracked in both characters and words (`usedChars`, `usedWords`).
- Seat limits block new activations, not already-active devices.
- Plan updates can flow from Gumroad webhooks and refresh-time re-verification.

## User Journey 1: New Purchase Activation
1. User buys plan on Gumroad and receives license key.
2. Desktop app calls `POST /api/license/activate` with `licenseKey`, `productId`, `deviceId`.
3. Server verifies with Gumroad and upserts license record with plan quota and seat limits.
4. Device activation is recorded and a signed session token is issued.

## User Journey 2: Returning User Refresh
1. Desktop app calls `POST /api/license/refresh` with token and device ID.
2. Server validates token and refreshes entitlement.
3. If client also sends `licenseKey` and `productId`, server re-verifies against Gumroad and syncs plan/status.

## User Journey 3: Upgrade or Downgrade Plan
1. Gumroad emits webhook after plan change.
2. Server deduplicates webhook, then updates matching license by `licenseKey` hash and/or `saleId`.
3. Plan attributes (`planCode`, `quotaChars`, `seatLimit`, `cycleType`, `commercial`) are updated.
4. User sees updated entitlement on next refresh without needing a brand-new license.

## User Journey 4: Cancellation, Refund, Chargeback
1. Gumroad webhook carries cancellation/refund signal.
2. License status is moved to `inactive` (or `revoked` where appropriate).
3. App refresh/status endpoints return inactive state and stop transcription access.

## User Journey 5: Seat Limit Handling
1. New activations are blocked once `activeSeats >= seatLimit`.
2. Existing already-active devices continue to function after plan seat reductions.
3. This avoids accidental lockout during downgrade transitions.

## User Journey 6: Quota Exhaustion and Reset
1. `consume` and proxy transcription charge characters atomically.
2. Monthly plans auto-reset usage counters at cycle boundary.
3. Lifetime plans never monthly-reset.
4. Admin top-up increases `bonusChars` instantly.

## User Journey 7: Duplicate Requests and Retries
1. Usage calls require `idempotencyKey`.
2. Duplicate idempotency keys return latest entitlement without double-charging.
3. Webhook events are deduplicated by event key/hash.

## User Journey 8: Concurrency Safety
1. Quota charging uses atomic conditions in MongoDB.
2. If parallel requests race, only valid in-quota updates persist.
3. Conflicting over-quota attempts are rejected and usage insert is rolled back.
