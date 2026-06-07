// i18n config: English as default (公司要求), 预留 zh-CN 兜底.
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import zh from "./locales/zh.json";

const stored = typeof window !== "undefined" ? localStorage.getItem("idm_lang") : null;
const browser =
  typeof navigator !== "undefined" ? navigator.language.split("-")[0] : "en";
const initial = stored || (browser === "zh" ? "zh" : "en");

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
  },
  lng: initial,
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export default i18n;
