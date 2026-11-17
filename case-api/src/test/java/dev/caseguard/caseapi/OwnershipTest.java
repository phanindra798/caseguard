package dev.caseguard.caseapi;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import dev.caseguard.caseapi.identity.CallerIdentity;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;

/**
 * Phase 1.1 acceptance tests: customers can read their own data, and NOTHING else.
 *
 * Seed data (db/seed/V1_1__demo_data.sql):
 *   acme / cust-A1: orders 1042, 1043, 1050, 1099
 *   acme / cust-A2: order 1200          (same tenant, different customer)
 *   globex / cust-G1: order 2001        (different tenant)
 */
@SpringBootTest
@ActiveProfiles("test")
@Import(FreshDatabaseConfig.class)
class OwnershipTest {

  @Autowired WebApplicationContext context;
  @Autowired JdbcTemplate jdbc;
  MockMvc mvc;

  @BeforeEach
  void setUp() {
    mvc = MockMvcBuilders.webAppContextSetup(context).build();
  }

  private ResultActions getAs(String tenant, String customer, String url) throws Exception {
    return mvc.perform(get(url)
        .header(CallerIdentity.TENANT_HEADER, tenant)
        .header(CallerIdentity.CUSTOMER_HEADER, customer));
  }

  // ---------- orders ----------

  @Test
  @DisplayName("owner can read their own order")
  void ownerReadsOwnOrder() throws Exception {
    getAs("acme", "cust-A1", "/orders/1042")
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.id").value("1042"))
        .andExpect(jsonPath("$.amountCents").value(12000))
        .andExpect(jsonPath("$.tenantId").doesNotExist());   // internal field never leaves the API
  }

  @Test
  @DisplayName("another tenant's order is 404")
  void otherTenantOrderIs404() throws Exception {
    getAs("acme", "cust-A1", "/orders/2001")
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("not_found"));
  }

  @Test
  @DisplayName("same tenant, different customer's order is 404")
  void otherCustomerSameTenantIs404() throws Exception {
    getAs("acme", "cust-A1", "/orders/1200").andExpect(status().isNotFound());
  }

  @Test
  @DisplayName("mismatched tenant/customer headers find nothing")
  void mismatchedIdentityIs404() throws Exception {
    // cust-A1 is an acme customer; claiming to be globex must not unlock anything
    getAs("globex", "cust-A1", "/orders/1042").andExpect(status().isNotFound());
  }

  @Test
  @DisplayName("'not yours' and 'does not exist' are indistinguishable")
  void notYoursLooksLikeMissing() throws Exception {
    String notYours = getAs("acme", "cust-A1", "/orders/2001").andReturn().getResponse().getContentAsString();
    String missing  = getAs("acme", "cust-A1", "/orders/9999").andReturn().getResponse().getContentAsString();
    // Only the echoed request path ("instance") may differ.
    assertThat(notYours.replace("/orders/2001", "X")).isEqualTo(missing.replace("/orders/9999", "X"));
  }

  @Test
  @DisplayName("missing identity headers are rejected with 400")
  void missingIdentityIs400() throws Exception {
    mvc.perform(get("/orders/1042"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("missing_identity"));
    mvc.perform(get("/orders/1042").header(CallerIdentity.TENANT_HEADER, "acme"))
        .andExpect(status().isBadRequest());
  }

  // ---------- cases ----------

  @Test
  @DisplayName("owner can read their own case, including its version")
  void ownerReadsOwnCase() throws Exception {
    getAs("acme", "cust-A1", "/cases/C-1042")
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.orderId").value("1042"))
        .andExpect(jsonPath("$.status").value("OPEN"))
        .andExpect(jsonPath("$.version").value(0));
  }

  @Test
  @DisplayName("another tenant's case is 404")
  void otherTenantCaseIs404() throws Exception {
    getAs("acme", "cust-A1", "/cases/C-2001").andExpect(status().isNotFound());
  }

  @Test
  @DisplayName("cases by order: own order returns its case, someone else's returns empty")
  void casesByOrder() throws Exception {
    getAs("acme", "cust-A1", "/cases?orderId=1042")
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.length()").value(1))
        .andExpect(jsonPath("$[0].id").value("C-1042"));

    getAs("acme", "cust-A1", "/cases?orderId=2001")
        .andExpect(status().isOk())
        .andExpect(content().json("[]"));
  }

  // ---------- database-level guarantee ----------

  @Test
  @DisplayName("the database itself rejects an order whose customer is in another tenant")
  void databaseRejectsCrossTenantOrder() {
    // Even if application code had a bug, the composite foreign key
    // (tenant_id, customer_id) -> customers(tenant_id, id) blocks this row.
    assertThatThrownBy(() -> jdbc.update("""
        insert into orders (id, tenant_id, customer_id, description, amount_cents, status, promised_date)
        values ('bad-1', 'globex', 'cust-A1', 'x', 100, 'PLACED', current_date)
        """))
        .isInstanceOf(DataIntegrityViolationException.class);
  }
}
