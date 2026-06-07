Feature: AI 1.0 — Glossary mapping + Owner verification workflow
  As a data steward
  I want LLM to suggest glossary bindings, and approve owners from AI suggestions
  So that documentation and ownership stay accurate with less manual work.

  Background:
    Given the IDM API is running
    And a clickhouse service "shop" with database "shop" and schema "default"
    And a table asset "shop.default.orders_daily" with 5 columns
    And a glossary term "GMV" defined as "Gross Merchandise Volume"
    And an owner "alice@example.com" verified for "shop.default.orders_daily"

  Scenario: map_glossary skill creates a pending suggestion
    When I run skill "map_glossary" with apply=false
    Then the response status is 200
    And the response body is valid JSON
    And the suggestion of type "glossary" is created for "shop.default.orders_daily"

  Scenario: Approve a glossary suggestion binds the term
    Given a pending "glossary" suggestion for "shop.default.orders_daily" with term "GMV"
    When I approve the latest suggestion
    Then the response status is 200
    And the table "shop.default.orders_daily" has glossary term "GMV" bound

  Scenario: Approve an "owner" suggestion verifies the owner
    Given a pending "owner" suggestion for "shop.default.orders_daily" with email "bob@example.com"
    When I approve the latest suggestion
    Then the response status is 200
    And the table "shop.default.orders_daily" has owner "bob@example.com" verified
