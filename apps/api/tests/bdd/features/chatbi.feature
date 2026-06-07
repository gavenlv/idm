Feature: ChatBI — Natural language to SQL
  As a business user
  I want to ask a question in natural language and get a SQL answer
  So that I can self-serve data without a data engineer.

  Background:
    Given the IDM API is running
    And a clickhouse service "shop" with database "shop" and schema "default"
    And a table asset "shop.default.orders_daily" with 5 columns

  Scenario: nl2sql skill generates SQL for a simple count
    When I run skill "nl2sql" with question "How many rows in orders_daily"
    Then the response status is 200
    And the nl2sql output reports a non-empty sql field

  Scenario: ChatBI API returns a guarded response
    When I ask ChatBI "How many rows in orders_daily"
    Then the response status is 200
    And the chatbi response includes a confidence field
