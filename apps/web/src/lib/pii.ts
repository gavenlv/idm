/**
 * PII 分类→颜色映射 (前端只做展示, 业务逻辑在 backend schemas.PII_CLASSES).
 * 风险等级: 高 (身份证/卡号/SSN/护照/手机/邮箱/地址) → 红;
 *         中 (姓名/出生日期/IP/坐标/车牌) → 橙;
 *         低 (uuid_pseudo / other) → 蓝.
 */
const HIGH_RISK = new Set(["id_card", "card_full", "ssn", "passport", "phone", "email", "address"]);
const MID_RISK = new Set(["name", "dob", "ip", "geo", "license_plate", "card_bin", "device_id"]);

export function piiColor(piiClass: string): string {
  if (piiClass === "none" || !piiClass) return "#999";
  if (HIGH_RISK.has(piiClass)) return "#d4380d";
  if (MID_RISK.has(piiClass)) return "#fa8c16";
  if (piiClass === "uuid_pseudo") return "#1f6feb";
  return "#722ed1";
}

export function piiRiskLabel(piiClass: string): "高" | "中" | "低" | "无" {
  if (HIGH_RISK.has(piiClass)) return "高";
  if (MID_RISK.has(piiClass)) return "中";
  if (piiClass === "none" || !piiClass) return "无";
  return "低";
}
