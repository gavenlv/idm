Feature: Use Case Trigger & Re-scan API (系统级入口)
  6 阶段管道的"业务入口" (use case) 与"系统入口" (source_type) 分开:
  - 业务入口: 业务人员按 use case 跑全 6 阶段 / 单阶段
  - 系统入口: 运维/平台按 source_type 扫资源
  - 重新扫描: 幂等 (asset 全部 upsert), 多次调用无副作用

  Scenario: Trigger a use case via the system API
    When I trigger use case "shop-orders-mex-pipeline" via the system API
    Then the response status is 200
    And the response body is valid JSON
    And the response body has an ok value of true
    And the response body has a use_case_id field

  Scenario: Rescan a use case is idempotent
    When I trigger use case "shop-orders-mex-pipeline" via the system API
    Then the response status is 200
    And the response body has an ok value of true
    When I rescan use case "shop-orders-mex-pipeline" via the system API
    Then the response status is 200
    And the response body has an ok value of true

  Scenario: Trigger a single stage of a use case
    When I trigger stage 3 of use case "shop-orders-mex-pipeline" via the system API
    Then the response status is 200
    And the response body is valid JSON
    And the response body has an ok value of true

  Scenario: Re-scan assets by source type (GCS)
    When I rescan assets of type "gcs" with bucket "company-raw" via the system API
    Then the response status is 200
    And the response body is valid JSON
    And the response body has an ok value of true

  Scenario: Trigger non-existent use case returns 404
    When I trigger use case "not-a-real-use-case" via the system API
    Then the response status is 404
