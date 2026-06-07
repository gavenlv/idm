Feature: AI governance — owners, glossary, and quality
  As a data steward
  I want AI to suggest owners, glossary terms, and surface quality issues
  So that I can govern the warehouse with confidence.

  Background:
    Given the IDM API is running
    And a clickhouse service "shop" with database "shop" and schema "default"
    And a table asset "shop.default.orders_daily" with 5 columns

  Scenario: Seeded owner is listed
    Given an owner "alice@example.com" verified for "shop.default.orders_daily"
    When I list owners for service "shop"
    Then the response status is 200
    And the response body is valid JSON
    And the response contains owner "alice@example.com"

  Scenario: Seeded glossary term is listed
    Given a glossary term "GMV" defined as "Gross Merchandise Volume"
    When I list glossary terms
    Then the response status is 200
    And the response body is valid JSON
    And the response contains glossary term "GMV"

  Scenario: detect_anomalies skill reports at least one finding
    When I run skill "detect_anomalies" with apply=false
    Then the response status is 2xx
    And the response body is valid JSON
    And the detect_anomalies output reports at least one anomaly kind

  Scenario: MCP sidecar health is exposed
    When I get health of MCP sidecars
    Then the response status is 200
    And the MCP health reports clickhouse status
