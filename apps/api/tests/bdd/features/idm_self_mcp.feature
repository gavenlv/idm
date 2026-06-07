Feature: idm-self MCP — External Agent access
  As an external agent (Claude / Cursor)
  I want to search assets, fetch lineage, and review AI suggestions via idm-self
  So that I can let LLMs work with the knowledge graph safely.

  Background:
    Given the IDM API is running
    And a clickhouse service "shop" with database "shop" and schema "default"
    And a table asset "shop.default.orders_daily" with 5 columns

  Scenario: List idm-self tools includes the search/get/impact tools
    When I list idm-self tools
    Then the response status is 200
    And the idm-self tool list contains "idm.search_assets"
    And the idm-self tool list contains "idm.get_lineage"
    And the idm-self tool list contains "idm.impact"

  Scenario: idm.search_assets returns the seeded table
    When I call idm-self tool "idm.search_assets" with q "orders"
    Then the response status is 200
    And the response body is valid JSON
    And the search result contains the table "shop.default.orders_daily"

  Scenario: idm.list_skills returns the registered skills
    When I call idm-self tool "idm.list_skills"
    Then the response status is 200
    And the skills list contains "discover_clickhouse_assets"
    And the skills list contains "nl2sql"
