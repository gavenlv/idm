Feature: Data Pipeline Lineage (GCS + Flink + ClickHouse + Superset)
  真实管道: GCS(原始) → Airflow + Flink 预处理 → GCS(model-input) → MEX(黑盒) → GCS(model-output) → Flink load → ClickHouse → Superset
  6 阶段: 1=上游预处理, 2=model-input, 3=MEX, 4=model-output, 5=load-to-CH, 6=Superset
  IDM 通过"读代码 + 读元数据"端到端学习这条管道

  Scenario: GCS MCP is registered and healthy
    When I list the available MCP servers
    Then the response status is 200
    And the MCP list contains "gcs"
    And the MCP list contains "flink"
    And the MCP list contains "superset_db"
    And the MCP list contains "airflow_db"

  Scenario: discover_gcs_assets skill is registered
    When I get the skills list
    Then the response status is 200
    And the skills list contains "discover_gcs_assets"
    And the skills list contains "parse_flink_job"
    And the skills list contains "parse_mex_io"
    And the skills list contains "analyze_data_pipeline"

  Scenario: discover_gcs_assets dry-run on empty bucket returns empty result
    When I run the skill "discover_gcs_assets" with inputs:
      | bucket | non-existent-bucket-12345 |
    Then the response status is 200
    And the response body is valid JSON
    And the skill output has an empty items list or a no objects summary

  Scenario: parse_flink_job without github token returns a clear error
    When I run the skill "parse_flink_job" with inputs:
      | repo | some-org/some-flink-jobs |
      | paths | ["jobs/orders_*.sql"] |
    Then the response status is 200
    And the response body is valid JSON
    And the response body has an ok value of false
    And the response body has an error containing "MCP_GITHUB_TOKEN"

  Scenario: parse_mex_io without github token returns a clear error
    When I run the skill "parse_mex_io" with inputs:
      | repo | some-org/some-mex-models |
      | paths | ["orders/io.yaml"] |
    Then the response status is 200
    And the response body is valid JSON
    And the response body has an ok value of false
    And the response body has an error containing "MCP_GITHUB_TOKEN"

  Scenario: analyze_data_pipeline with no use_case returns an error
    When I run the skill "analyze_data_pipeline" with inputs:
      | apply | false |
    Then the response status is 200
    And the response body is valid JSON
    And the response body has an ok value of false
