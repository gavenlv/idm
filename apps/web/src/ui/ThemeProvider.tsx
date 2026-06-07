import { createContext, useContext, type ReactNode } from "react";

/**
 * IDM ThemeProvider (占位, M1 S1.3 接入公司 Design Token).
 */
const ThemeContext = createContext<{ theme: "light" | "dark" }>({ theme: "light" });

export function ThemeProvider({ children }: { children: ReactNode }) {
  return <ThemeContext.Provider value={{ theme: "light" }}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);
