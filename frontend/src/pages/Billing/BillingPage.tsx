import { useState, useEffect } from "react";
import { useAuth } from "../../context/AuthContext";
import { subscribe, getProfile, getUsageStats, type UsageStats } from "../../api/client";

interface PlanInfo {
  tier: string;
  name: string;
  price: string;
  priceDetail: string;
  badge?: string;
  features: string[];
}

const PLANS: PlanInfo[] = [
  {
    tier: "solo",
    name: "Solo",
    price: "$29",
    priceDetail: "/ month",
    features: [
      "1 user",
      "5 data connectors",
      "10 pipeline runs / day",
      "5 GB storage",
      "Email support",
    ],
  },
  {
    tier: "lab",
    name: "Lab",
    price: "$99",
    priceDetail: "/ month",
    badge: "Most Popular",
    features: [
      "Up to 5 users",
      "All data connectors",
      "Unlimited pipeline runs",
      "50 GB storage",
      "Team collaboration",
      "Priority email support",
    ],
  },
  {
    tier: "institution",
    name: "Institution",
    price: "Custom",
    priceDetail: "contact us",
    features: [
      "Unlimited users",
      "All data connectors",
      "Unlimited pipeline runs",
      "Unlimited storage",
      "Custom connector development",
      "Priority support with SLA",
      "SSO / SAML integration",
    ],
  },
];

export default function BillingPage() {
  const { user } = useAuth();
  const [switching, setSwitching] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [usageStats, setUsageStats] = useState<UsageStats | null>(null);

  const currentTier = user?.subscription_tier ?? "solo";

  useEffect(() => {
    getUsageStats().then(setUsageStats).catch(() => {
      // Fallback if not authenticated or endpoint unavailable
      setUsageStats({
        runs_this_month: 0,
        runs_limit: currentTier === "solo" ? 300 : null,
        storage_used_gb: 0,
        storage_limit: currentTier === "solo" ? 5 : currentTier === "lab" ? 50 : null,
      });
    });
  }, [currentTier]);

  const handleSubscribe = async (tier: string) => {
    if (tier === currentTier) return;
    if (tier === "institution") return; // contact sales
    setSwitching(tier);
    setError(null);
    setSuccess(null);
    try {
      await subscribe(tier);
      // Refresh profile so the context picks up the new tier
      await getProfile();
      setSuccess(`Successfully switched to the ${tier.charAt(0).toUpperCase() + tier.slice(1)} plan.`);
      // Reload to refresh user context globally
      window.location.reload();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to update subscription.";
      setError(message);
    } finally {
      setSwitching(null);
    }
  };

  return (
    <div className="billing-page">
      <h1>Subscription &amp; Billing</h1>
      <p className="billing-subtitle">
        Manage your plan and monitor your usage.
      </p>

      {error && <div className="billing-alert billing-alert-error">{error}</div>}
      {success && <div className="billing-alert billing-alert-success">{success}</div>}

      {/* ── Plan Cards ── */}
      <div className="plan-cards">
        {PLANS.map((plan) => {
          const isCurrent = plan.tier === currentTier;
          return (
            <div
              key={plan.tier}
              className={`plan-card${isCurrent ? " current" : ""}`}
            >
              {plan.badge && (
                <span className="plan-badge">{plan.badge}</span>
              )}
              {isCurrent && (
                <span className="plan-badge plan-badge-current">Current Plan</span>
              )}
              <h2 className="plan-name">{plan.name}</h2>
              <div className="plan-price">
                {plan.price}
                <span className="plan-price-detail">{plan.priceDetail}</span>
              </div>
              <ul className="plan-features">
                {plan.features.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
              <div className="plan-action">
                {isCurrent ? (
                  <button className="btn-primary" disabled>
                    Current Plan
                  </button>
                ) : plan.tier === "institution" ? (
                  <button
                    className="btn-secondary"
                    onClick={() =>
                      window.open("mailto:sales@astroplatform.io", "_blank")
                    }
                  >
                    Contact Sales
                  </button>
                ) : (
                  <button
                    className="btn-primary"
                    disabled={switching !== null}
                    onClick={() => handleSubscribe(plan.tier)}
                  >
                    {switching === plan.tier
                      ? "Processing..."
                      : PLANS.findIndex((p) => p.tier === currentTier) <
                          PLANS.findIndex((p) => p.tier === plan.tier)
                        ? "Upgrade"
                        : "Switch Plan"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Usage Stats ── */}
      {usageStats && (
        <section className="usage-section">
          <h2>Current Usage</h2>
          <div className="usage-grid">
            <div className="usage-item">
              <div className="usage-label">Pipeline Runs This Month</div>
              <div className="usage-value">
                {usageStats.runs_this_month}
                {usageStats.runs_limit !== null && (
                  <span className="usage-limit"> / {usageStats.runs_limit}</span>
                )}
                {usageStats.runs_limit === null && (
                  <span className="usage-limit"> (unlimited)</span>
                )}
              </div>
              {usageStats.runs_limit !== null && (
                <div className="usage-bar">
                  <div
                    className="usage-bar-fill"
                    style={{
                      width: `${Math.min(
                        (usageStats.runs_this_month / usageStats.runs_limit) * 100,
                        100
                      )}%`,
                    }}
                  />
                </div>
              )}
            </div>
            <div className="usage-item">
              <div className="usage-label">Storage Used</div>
              <div className="usage-value">
                {usageStats.storage_used_gb} GB
                {usageStats.storage_limit !== null && (
                  <span className="usage-limit">
                    {" "}
                    / {usageStats.storage_limit} GB
                  </span>
                )}
                {usageStats.storage_limit === null && (
                  <span className="usage-limit"> (unlimited)</span>
                )}
              </div>
              {usageStats.storage_limit !== null && (
                <div className="usage-bar">
                  <div
                    className="usage-bar-fill"
                    style={{
                      width: `${Math.min(
                        (usageStats.storage_used_gb / usageStats.storage_limit) * 100,
                        100
                      )}%`,
                    }}
                  />
                </div>
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
