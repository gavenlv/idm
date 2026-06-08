Feature: Data Quality — Dashboard, rules, profiling, and insights
  As a data steward
  I want a quality dashboard, custom rules, and auto profiling
  So that I can detect and act on data issues before they reach the business.

  Background:
    Given the IDM API is running
    And a clickhouse service "shop" with database "shop" and schema "default"
    And a table asset "shop.default.orders_daily" with 5 columns

  Scenario: Quality dashboard returns the summary shape
    When I get the quality dashboard
    Then the response status is 200
    And the response body is valid JSON
    And the quality dashboard has the tables_total field
    And the quality dashboard has the rules_total field

  Scenario: Create a freshness rule and list it
    When I create a freshness rule for "shop.default.orders_daily"
    Then the response status is 201
    When I list quality rules for "shop.default.orders_daily"
    Then the response status is 200
    And the response body is valid JSON
    And the rules list contains at least 1 rule

  Scenario: Profiler skill produces a row_count for the table
    When I run skill "profiler" with sample_rows=10
    Then the response status is 2xx
    And the response body is valid JSON
    And the profiler output reports at least 1 profiled table

  Scenario: compose_insight skill emits a pending insight
    When I run skill "compose_insight" with days=7
    Then the response status is 2xx
    And the response body is valid JSON
    And the compose_insight output reports at least 1 finding
