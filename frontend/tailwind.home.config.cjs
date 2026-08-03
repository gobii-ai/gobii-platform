const baseConfig = require('./tailwind.config.cjs')

module.exports = {
  ...baseConfig,
  content: [
    '../templates/base.html',
    '../templates/account/_login_content.html',
    '../templates/account/_modal_email_start_content.html',
    '../templates/account/_signup_content.html',
    '../templates/account/_social_auth_buttons.html',
    '../templates/account/modal_login.html',
    '../templates/account/modal_signup.html',
    '../templates/includes/_gk_auth.html',
    '../templates/includes/_gk_showcase.html',
    '../templates/includes/_gk_theme.html',
    '../templates/includes/_header_logo_image.html',
    '../templates/includes/_signup_tracking.html',
    '../templates/includes/_unified_header_nav.html',
    '../templates/includes/_unified_header_nav_mini.html',
    '../templates/partials/_csrf_helpers.html',
    '../templates/partials/_cta_signup_modal.html',
    '../templates/partials/_generic_modal.html',
    '../templates/partials/_immersive_overlay.html',
    '../templates/partials/_immersive_overlay_script.html',
    '../templates/partials/_immersive_overlay_styles.html',
    // Only the default K presentation uses this scoped bundle. Legacy/custom-spawn
    // homepage renders deliberately retain the full global Tailwind stylesheet.
    '../pages/templates/home/_k_*.html',
    '../static/js/account_auth_forms.js',
    '../static/js/cta_signup_modal.js',
  ],
  safelist: [
    ...baseConfig.safelist,
    // The shared homepage script can toggle these when an intelligence selector is present.
    'mt-1.5',
    'bg-indigo-50/40',
  ],
}
