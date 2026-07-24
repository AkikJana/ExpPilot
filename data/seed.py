"""Seeds the flag registry, historical experiment store, and demo experiments. Idempotent."""
from __future__ import annotations

import json

from data.db import get_conn, init_db
from data.synth import make_experiment

# (key, segment, status, running_experiment_id)
FLAGS: list[tuple[str, str, str, str | None]] = [
    ("oneshop_checkout_v2", "mobile_users", "in_use", "demo_checkout_true_lift"),
    ("oneshop_checkout_simplified", "mobile_users", "free", None),
    ("oneshop_guest_checkout", "all_users", "free", None),
    ("oneapp_bundle_nudge", "device_upgrade_eligible", "in_use", "demo_bundle_srm"),
    ("oneapp_device_bundle_carousel", "device_upgrade_eligible", "free", None),
    ("oneapp_bundle_pricing_badge", "device_upgrade_eligible", "free", None),
    ("plan_upgrade_paywall_copy", "plan_browsers", "in_use", "demo_paywall_guardrail"),
    ("plan_upgrade_progress_bar", "plan_browsers", "free", None),
    ("plan_comparison_table_v2", "plan_browsers", "free", None),
    ("churn_save_offer_timing", "at_risk_users", "free", None),
    ("churn_retention_discount_banner", "at_risk_users", "free", None),
    ("churn_winback_email_cta", "lapsed_users", "free", None),
    ("onboarding_welcome_flow_v2", "new_users", "free", None),
    ("onboarding_progressive_profiling", "new_users", "free", None),
    ("payments_autopay_nudge", "billing_users", "free", None),
    ("payments_saved_card_default", "billing_users", "free", None),
    ("support_chatbot_entrypoint", "all_users", "free", None),
    ("loyalty_points_display", "loyalty_members", "free", None),
    ("referral_share_incentive", "all_users", "free", None),
    ("notification_opt_in_prompt", "mobile_users", "free", None),
]

# (id, category, hypothesis_text, lift_observed, outcome)
HISTORY: list[tuple[str, str, str, float, str]] = [
    ("hist_ck001", "checkout", "One-page checkout reduces mobile drop-off vs. 3-step flow", 0.031, "shipped"),
    ("hist_ck002", "checkout", "Guest checkout option lifts conversion for new mobile users", 0.024, "shipped"),
    ("hist_ck003", "checkout", "Autofill saved address reduces checkout abandonment", 0.012, "shipped"),
    ("hist_ck004", "checkout", "Progress indicator on checkout steps improves completion", 0.004, "abandoned"),
    ("hist_ck005", "checkout", "Removing coupon field from checkout increases conversion", -0.008, "rolled_back"),
    ("hist_ck006", "checkout", "Apple Pay entrypoint above fold lifts mobile checkout conversion", 0.028, "shipped"),
    ("hist_bn001", "device_bundles", "Bundle nudge banner on OneApp home lifts device upgrade CTR", 0.019, "shipped"),
    ("hist_bn002", "device_bundles", "Carousel of bundle deals increases add-to-cart rate", 0.015, "shipped"),
    ("hist_bn003", "device_bundles", "Pricing badge (Save X%) on bundles increases conversion", 0.021, "shipped"),
    ("hist_bn004", "device_bundles", "Auto-recommend bundle at cart increases average order value", 0.006, "abandoned"),
    ("hist_bn005", "device_bundles", "Countdown timer on bundle offer increases urgency conversion", -0.011, "rolled_back"),
    ("hist_pu001", "plan_upgrades", "Simplified plan comparison table increases upgrade rate", 0.017, "shipped"),
    ("hist_pu002", "plan_upgrades", "Paywall copy emphasizing savings increases upgrade conversion", 0.022, "shipped"),
    ("hist_pu003", "plan_upgrades", "Progress bar toward next plan tier increases upgrade rate", 0.009, "abandoned"),
    ("hist_pu004", "plan_upgrades", "Social proof ('80% of users upgraded') increases plan upgrades", 0.014, "shipped"),
    ("hist_pu005", "plan_upgrades", "Aggressive upsell modal on login increases upgrades short-term", 0.026, "rolled_back"),
    ("hist_ch001", "churn", "Personalized save offer at cancellation reduces churn", 0.033, "shipped"),
    ("hist_ch002", "churn", "Earlier-timed retention offer (day 3 vs day 7) reduces churn", 0.018, "shipped"),
    ("hist_ch003", "churn", "Winback email with discount CTA improves reactivation rate", 0.011, "shipped"),
    ("hist_ch004", "churn", "Retention discount banner on billing page reduces churn", 0.003, "abandoned"),
    ("hist_ch005", "churn", "Exit-survey-gated cancellation flow reduces completed churn", -0.014, "rolled_back"),
    ("hist_on001", "onboarding", "Simplified welcome flow (3 steps vs 6) increases activation", 0.029, "shipped"),
    ("hist_on002", "onboarding", "Progressive profiling reduces signup abandonment", 0.016, "shipped"),
    ("hist_on003", "onboarding", "Gamified onboarding checklist increases feature adoption", 0.008, "abandoned"),
    ("hist_on004", "onboarding", "Video walkthrough on first login increases activation", 0.013, "shipped"),
    ("hist_on005", "onboarding", "Mandatory tutorial before app access reduces day-1 retention", -0.019, "rolled_back"),
    ("hist_pm001", "payments", "Autopay nudge at billing screen increases autopay enrollment", 0.041, "shipped"),
    ("hist_pm002", "payments", "Saved card as default payment increases repeat purchase rate", 0.020, "shipped"),
    ("hist_pm003", "payments", "Extra payment confirmation step reduces failed transactions", 0.007, "abandoned"),
    ("hist_pm004", "payments", "One-click reorder reduces payment page drop-off", 0.023, "shipped"),
]


def seed_flags(conn) -> None:
    """Insert or replace the 20-flag registry."""
    conn.executemany(
        "INSERT OR REPLACE INTO flags (key, segment, status, running_experiment_id) VALUES (?, ?, ?, ?)",
        FLAGS,
    )


def seed_history(conn) -> None:
    """Insert or replace the 30 historical telco-commerce experiments."""
    conn.executemany(
        "INSERT OR REPLACE INTO history (id, category, hypothesis_text, lift_observed, outcome) "
        "VALUES (?, ?, ?, ?, ?)",
        HISTORY,
    )


def seed_demo_experiments(conn) -> None:
    """Seed three running demo experiments (one per flag marked in_use) at day 1."""
    demo_specs = [
        ("demo_checkout_true_lift", "true_lift", 1001),
        ("demo_bundle_srm", "srm", 1002),
        ("demo_paywall_guardrail", "guardrail_breach", 1003),
    ]
    for demo_id, scenario, seed in demo_specs:
        config, day_stats, ground_truth = make_experiment(scenario, seed)
        config_dict = config.model_dump()
        config_dict["id"] = demo_id
        conn.execute(
            "INSERT OR REPLACE INTO experiments (id, config, status, ground_truth) VALUES (?, ?, ?, ?)",
            (demo_id, json.dumps(config_dict), "running", json.dumps(ground_truth)),
        )
        day1 = day_stats[0].model_dump()
        day1["experiment_id"] = demo_id
        conn.execute(
            "INSERT OR REPLACE INTO day_stats (experiment_id, day, data) VALUES (?, ?, ?)",
            (demo_id, 1, json.dumps(day1)),
        )


def main() -> None:
    """Initialize the schema and idempotently seed flags, history, and demo experiments."""
    init_db()
    conn = get_conn()
    try:
        seed_flags(conn)
        seed_history(conn)
        seed_demo_experiments(conn)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
