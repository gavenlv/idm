import { useTranslation } from "react-i18next";
import { Select } from "./Input";

export function LanguageSwitcher() {
  const { i18n } = useTranslation();
  return (
    <Select
      value={i18n.language}
      onChange={(e) => {
        i18n.changeLanguage(e.target.value);
        try {
          localStorage.setItem("idm_lang", e.target.value);
        } catch {
          /* noop */
        }
      }}
      style={{ width: 110 }}
      aria-label="Language"
    >
      <option value="en">English</option>
      <option value="zh">中文</option>
    </Select>
  );
}
