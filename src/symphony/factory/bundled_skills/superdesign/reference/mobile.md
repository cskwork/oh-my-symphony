# mobile - mobile / native medium

Load at Build when medium = mobile/native. Net-new vs web taste rules; pairs with `taste-core.md` (universal bans still hold).

## Platform authority

- **iOS**: Apple Human Interface Guidelines. SF Pro / SF Symbols, native nav bars + tab bars, large titles, dynamic type, safe-area insets, sheet/modal presentation, haptics on key actions.
- **Android**: Material 3 (Expressive where it fits). Dynamic color, M3 tokens, large shape families, spring motion, FAB + nav rail/bar, edge-to-edge with system bar insets.
- Respect each platform's conventions; do not ship iOS chrome on Android or vice versa.

## Cross-platform stacks

- React Native (+ Expo): `StyleSheet`/NativeWind, `react-navigation`, Reanimated for motion, platform-split where UX diverges.
- SwiftUI: native components, `@Environment` for color scheme + dynamic type, `.safeAreaInset`.
- Jetpack Compose: Material 3 composables, `WindowInsets`, dynamic color.

## Mobile-specific rules

- Touch targets >= 44x44pt (iOS) / 48x48dp (Android). Thumb-reach: primary actions in the lower zone.
- One-hand layout; bottom sheets over top menus. Respect safe areas + notch/Dynamic Island; never hardcode status-bar height.
- Type scales with the platform dynamic-type setting. Test smallest + largest. Honor reduce-motion + reduce-transparency.
- CJK/Korean (see `taste-core.md`): a real CJK face (Pretendard / Noto Sans KR, system CJK on native), word (어절) line-breaking with no mid-word wrap, natural copy with correct particles. Korean labels run longer than English, so size buttons/tabs to fit both without truncation.
- Gestures have visible affordance + a button fallback. Loading/empty/error/offline states per screen.
- Thumb zone: keep the primary action + nav in the easy lower/center arc (bottom tab bar over a top bar); a primary or add-to-cart CTA stays sticky in the thumb zone while scrolling. Relocate or duplicate any primary action stranded in the hard top corners on tall phones. Bottom sheets for secondary content. Honest gesture vocabulary (swipe actions, pull-to-refresh, long-press, drag-to-dismiss sheets) - each with an affordance + button fallback, taught with a subtle first-use hint.
- Haptics as feedback: fire distinct haptics on key actions and gesture commits (confirm, long-press menu, end-of-list) so the action is felt before it is seen. Purposeful only - no buzz-on-everything.
- Light + dark via platform color scheme. Off-black/off-white, not pure.

## Assets

App icons + screens via `assets.md` chain. Icon sets: SF Symbols (iOS), Material Symbols (Android), or one cross-platform family. No hand-rolled icon paths.
