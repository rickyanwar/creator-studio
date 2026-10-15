# Facebook App Card (com.facebook.katana)

- **Version**: 1.0.0
- **Package**: `com.facebook.katana`
- **Platform**: Android
- **Role**: Guidance for agent UI interaction (application code strictly enforces execution gates).

---

## 1. Profile and Managed Pages Hierarchy
- The logged-in personal account is the root **Main Profile**.
- **Managed Pages** do not possess independent credentials; they exist under the Main Profile.
- To act as a Page, navigate to the top-right menu / profile switcher icon and switch active context to the target Page.
- Before performing any publishing action, re-verify that the active avatar/name matches the requested destination.

## 2. Identity Verification
- Locate the active identity displayed at the top bar or in the menu drawer.
- Compare the visible name against the expected actor (Main Profile name or Page name).
- If the visible identity does not match and cannot be switched via the profile switcher, halt immediately and report `identity_unverified`.
- Never post under the Main Profile when the target is a Page, and vice versa.

## 3. Dynamic UI Controls
- Facebook frequently changes UI layouts, button placements, and test variants (A/B testing).
- **Never use hard-coded screen coordinates**.
- Locate UI elements dynamically via:
  - Accessibility content descriptions (e.g., "Menu", "Profile", "Switch profile", "Photo/video").
  - Text anchors (e.g., "What's on your mind?", "Create a post", "Next", "Done").
  - Visual hierarchy using vision + accessibility tree.
- Allow natural scrolling with gentle swipe gestures if an element is off-screen.

## 4. Permission Dialogs
- If Android system permission dialogs appear (e.g., Photos and Videos, Notifications):
  - For media access: Select "Allow" or "Allow all photos" if required to select content.
  - For notifications or location: Select "Don't allow" or dismiss.
- If Facebook in-app prompts appear (e.g., "Connect accounts", "Add phone number", "Turn on notifications"):
  - Select "Not now", "Dismiss", or the close icon (X).

## 5. Dry-Run Mandatory Stop
- **CRITICAL SAFETY DIRECTIVE**:
  - When the instruction indicates a **dry-run** or **probe/verification**:
  - Stop immediately once the composer is open and actor identity is confirmed.
  - **UNDER NO CIRCUMSTANCES TAP "Post", "Publish", OR ANY SHARE BUTTON**.
  - Capture confirmation that composer is accessible and actor matches, then terminate the run cleanly.

## 6. Guarded Publishing Workflow (P1)
- **Composer Preparation**:
  - Open composer via "What's on your mind?" or Page post prompt.
  - If caption text provided, input text into composer edit field.
  - If media attachments staged, tap "Photo/video" and select staged media files.
  - Stop and allow host runner gate to verify actor, caption text, and media preview from raw UI accessibility tree.
- **Single Submission Directive**:
  - Submit by tapping "Post", "Publish", or "Share" exactly once.
  - Never double-tap or automatically retry submission.
- **Post Identity Evidence Capture**:
  - After submission, capture visible post ID or permalink from notifications, feed updates, or top activity details.
  - If post identity cannot be proven from device evidence, host runner marks delivery `uncertain` with `publish_may_have_started=true`.

## 7. Architecture & Gate Enforcement
- This App Card provides contextual guidance to the vision agent.
- The host runner application and backend orchestrator maintain exclusive ownership of safety gates, timeouts, subprocess execution, and fail-closed policies.
