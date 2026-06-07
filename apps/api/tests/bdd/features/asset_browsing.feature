Feature: Browse and inspect data assets
  As a data steward
  I want to browse the assets registered in IDM
  So that I can find and understand tables in the warehouse.

  Background:
    Given the IDM API is running
    And a clickhouse service "shop" with database "shop" and schema "default"
    And a table asset "shop.default.orders_daily" with 5 columns

  Scenario: List assets returns the seeded table
    When I list assets
    Then the response status is 200
    And the response body is valid JSON
    And the response contains at least 1 asset
    And the response contains the table "shop.default.orders_daily"

  Scenario: Search globally finds the table
    When I search globally for "orders"
    Then the response status is 2xx
    And the response body is valid JSON
    And the search returns at least 1 hit
