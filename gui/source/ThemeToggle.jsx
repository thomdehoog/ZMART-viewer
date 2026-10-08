import React from "react";
import { topBarStyles as styles } from "./top-bar-styles.js";

// Where the chosen theme is remembered between sessions. main.jsx reads the
// same key before anything is drawn, so a page that was left light comes
// back light with no dark flash on the way.
const THEME_KEY = "zmart-theme";

/**
 * The button that swaps the interface between its dark and light dress.
 *
 * It sits in the top bar beside the 2-D/3-D toggle and Overview, and its face
 * names the theme it would switch TO -- press "Light" and the panels go
 * light. Only the chrome changes: the image area stays black in both themes,
 * because fluorescence is read against black, and the colours chosen for
 * channels and targets are the operator's own and are not touched.
 *
 * The switch itself is one attribute on the page. Every colour in the
 * interface is a named variable (see theme.css), and `data-theme` on the
 * <html> element decides which set of values those names take.
 */
function ThemeToggle() {
  const [theme, setTheme] = React.useState(
    () => (localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"),
  );
  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);
  const other = theme === "dark" ? "light" : "dark";
  return (
    <button
      onClick={() => setTheme(other)}
      style={{ ...styles.button, ...styles.themeToggle }}
      aria-label={`switch to the ${other} theme`}
      title={other === "light"
        ? "Dress the controls in light colours. The image itself stays on black"
        : "Dress the controls in dark colours again"}
    >
      {other === "light" ? "light-mode" : "dark-mode"}
    </button>
  );
}

export default ThemeToggle;
