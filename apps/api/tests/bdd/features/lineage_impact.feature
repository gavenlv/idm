Feature: Knowledge Graph — Lineage + Impact analysis
  As a data engineer
  I want to know upstream and downstream of any table, and who is affected by changes
  So that I can confidently refactor and roll out changes.

  Background:
    Given the IDM API is running
    And a clickhouse service "shop" with database "shop" and schema "default"
    And a table asset "shop.default.orders_daily" with 5 columns
    And a table asset "shop.default.orders_summary" with 3 columns
    And a lineage edge from "shop.default.orders_daily" to "shop.default.orders_summary"

  Scenario: Asset lineage API returns the seeded edge
    When I get lineage of "shop.default.orders_daily"
    Then the response status is 200
    And the response body is valid JSON
    And the lineage response includes a downstream edge to "shop.default.orders_summary"

  Scenario: Impact analysis reports downstream and affected owners
    Given an owner "alice@example.com" verified for "shop.default.orders_summary"
    When I get impact for "shop.default.orders_daily" direction "downstream" depth 2
    Then the response status is 200
    And the response body is valid JSON
    And the impact response includes downstream count >= 1
    And the impact response includes affected owner "alice@example.com"

  Scenario: extract_sql_lineage skill parses a simple SELECT INTO
    When I run extract_sql_lineage with downstream "shop.default.orders_summary"
    Then the response status is 200
    And the extract_sql_lineage output reports upstream tables
