/** Dark theme for Clerk's hosted components, from the approved tokens. Clerk renders the fields; we never see passwords. */
export const clerkAppearance = {
  variables: {
    colorBackground: "#0f1d2c",
    colorInputBackground: "#0a1420",
    colorText: "#e6eef4",
    colorTextSecondary: "#a3b8c8",
    colorInputText: "#e6eef4",
    colorPrimary: "#f2b64a",
    colorTextOnPrimaryBackground: "#0a1420",
    colorDanger: "#ff6a55",
    colorNeutral: "#e6eef4",
    borderRadius: "4px",
    fontFamily: '"Public Sans", system-ui, sans-serif',
    fontSize: "14px",
  },
} as const;

/** Headings on Clerk's sign-in and sign-up cards. Set on ClerkProvider, which is where Clerk reads localization. */
export const clerkLocalization = {
  signIn: {
    start: {
      title: "Sign in to UptimeTrace",
      subtitle: "See whether your alerts would catch the next outage.",
    },
  },
  signUp: {
    start: {
      title: "Create your UptimeTrace account",
      subtitle: "Connect your first host and test its alerts.",
    },
  },
} as const;
