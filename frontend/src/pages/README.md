# Frontend page ownership

- malek/: opportunities, NGO dashboard, admin approvals.
- ahmad/: applications, applicants and decisions.
- tarek/: recommendations.

App.jsx owns navigation and route registration.
LoginScreen.jsx preserves the initial Cognito sign-in flow.
aws-config.js configures the existing Cognito resources.

Pages currently contain placeholders, not backend integrations.
Do not treat empty placeholders as successful API results.

Routes use HashRouter, e.g. /#/applications, so static hosting does
not need path rewrites for these routes.

Cognito groups control frontend navigation only.
The backend must independently validate tokens, roles, approval
status, and ownership on every protected operation.

Coordinate shared App.jsx changes before merging.
