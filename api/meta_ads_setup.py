META_ADS_SETUP_INSTRUCTIONS = (
    "Register as a Meta developer, create a Business app with the Marketing API product, create a system user, "
    "assign the app and ad account, generate a system user token with ads_read access, and then connect Meta Ads "
    "from the integrations page."
)

META_ADS_SETUP_STEPS = (
    "Register the real Facebook admin account as a Meta developer before trying to create the app.",
    "Create a Business app and make sure the Marketing API product is actually added to that app.",
    "Capture the App ID and App Secret from App Settings -> Basic.",
    "Create a system user in Business Settings and assign the app plus the ad account to it.",
    "Generate a system user token with ads_read access. If Meta sends it for approval, another business admin must approve it in Business Settings -> Requests.",
    "Open the Meta Ads integration form and enter the App ID, App Secret, system user token, and default ad account ID.",
    "Optional but recommended for serious performance monitoring: add the Pixel or dataset ID so the agent can monitor conversion quality and event health.",
)

META_ADS_TROUBLESHOOTING_TIPS = (
    "If developers.facebook.com/apps keeps bouncing to a marketing or public landing page, complete developer registration first.",
    "Do not use the Meta app flow that says 'Create & manage app ads with Meta Ads Manager' because it does not include Marketing API.",
    "If token generation says approval was requested, the setup is not broken. Another business admin must approve it in Business Settings -> Requests.",
    "If the token works but no ad accounts are returned, double-check that the system user was assigned both the app and the ad account.",
    "If conversion-quality monitoring fails, make sure the system user or token also has access to the Pixel or dataset in Business Manager.",
)
