// Backend paths, relative to the Axios `baseURL` (see `@/api/client`).
// Every entry was verified against the routers in backend/app/api.
// All backend resource IDs are UUIDs, represented here as strings.
const API_PREFIX = '/api/v1';

type Id = string;

const PROPERTIES = `${API_PREFIX}/properties`;
const TENANT_PORTAL = `${API_PREFIX}/tenant-portal`;

const property = (propertyId: Id) => `${PROPERTIES}/${propertyId}`;
const room = (propertyId: Id, roomId: Id) => `${property(propertyId)}/rooms/${roomId}`;
const obligation = (propertyId: Id, obligationId: Id) =>
  `${property(propertyId)}/rent-obligations/${obligationId}`;

export const ENDPOINTS = {
  // Owner authentication (auth.py)
  auth: {
    register: `${API_PREFIX}/auth/register`,
    login: `${API_PREFIX}/auth/login`,
    me: `${API_PREFIX}/auth/me`,
  },

  // Tenant authentication (tenant_auth.py)
  tenantAuth: {
    register: `${API_PREFIX}/tenant-auth/register`,
    login: `${API_PREFIX}/tenant-auth/login`,
    activate: `${API_PREFIX}/tenant-auth/activate`,
    me: `${API_PREFIX}/tenant-auth/me`,
    password: `${API_PREFIX}/tenant-auth/me/password`,
    property: `${API_PREFIX}/tenant-auth/me/property`,
  },

  // Owner: properties (properties.py)
  properties: {
    list: PROPERTIES,
    detail: property,
  },

  // Owner: rooms (rooms.py)
  rooms: {
    list: (propertyId: Id) => `${property(propertyId)}/rooms`,
    detail: room,
  },

  // Owner: beds (beds.py)
  beds: {
    list: (propertyId: Id, roomId: Id) => `${room(propertyId, roomId)}/beds`,
    detail: (propertyId: Id, roomId: Id, bedId: Id) =>
      `${room(propertyId, roomId)}/beds/${bedId}`,
  },

  // Owner: tenants of a property (property_tenants.py)
  propertyTenants: {
    list: (propertyId: Id) => `${property(propertyId)}/tenants`,
    detail: (propertyId: Id, tenantId: Id) => `${property(propertyId)}/tenants/${tenantId}`,
  },

  // Public, unauthenticated property browsing (public_properties.py)
  publicProperties: {
    list: `${API_PREFIX}/public/properties`,
    nearby: `${API_PREFIX}/public/properties/nearby`,
    detail: (propertyId: Id) => `${API_PREFIX}/public/properties/${propertyId}`,
    rooms: (propertyId: Id) => `${API_PREFIX}/public/properties/${propertyId}/rooms`,
    availability: (propertyId: Id) =>
      `${API_PREFIX}/public/properties/${propertyId}/availability`,
  },

  // Owner: booking requests (property_stays.py)
  bookingRequests: {
    list: (propertyId: Id) => `${property(propertyId)}/booking-requests`,
    detail: (propertyId: Id, requestId: Id) =>
      `${property(propertyId)}/booking-requests/${requestId}`,
    approve: (propertyId: Id, requestId: Id) =>
      `${property(propertyId)}/booking-requests/${requestId}/approve`,
    reject: (propertyId: Id, requestId: Id) =>
      `${property(propertyId)}/booking-requests/${requestId}/reject`,
  },

  // Owner: stays (property_stays.py)
  stays: {
    list: (propertyId: Id) => `${property(propertyId)}/stays`,
    detail: (propertyId: Id, stayId: Id) => `${property(propertyId)}/stays/${stayId}`,
    checkIn: (propertyId: Id, stayId: Id) => `${property(propertyId)}/stays/${stayId}/check-in`,
    checkOut: (propertyId: Id, stayId: Id) =>
      `${property(propertyId)}/stays/${stayId}/check-out`,
    cancel: (propertyId: Id, stayId: Id) => `${property(propertyId)}/stays/${stayId}/cancel`,
    noShow: (propertyId: Id, stayId: Id) => `${property(propertyId)}/stays/${stayId}/no-show`,
  },

  // Owner: bed change requests (property_stays.py)
  bedChangeRequests: {
    list: (propertyId: Id) => `${property(propertyId)}/bed-change-requests`,
    detail: (propertyId: Id, requestId: Id) =>
      `${property(propertyId)}/bed-change-requests/${requestId}`,
    approve: (propertyId: Id, requestId: Id) =>
      `${property(propertyId)}/bed-change-requests/${requestId}/approve`,
    reject: (propertyId: Id, requestId: Id) =>
      `${property(propertyId)}/bed-change-requests/${requestId}/reject`,
  },

  // Owner: rent obligations, payments, adjustments and payment claims (rent.py)
  rent: {
    createObligation: (propertyId: Id, stayId: Id) =>
      `${property(propertyId)}/stays/${stayId}/rent-obligations`,
    obligations: (propertyId: Id) => `${property(propertyId)}/rent-obligations`,
    obligation,
    payments: (propertyId: Id, obligationId: Id) =>
      `${obligation(propertyId, obligationId)}/payments`,
    reversePayment: (propertyId: Id, obligationId: Id, paymentId: Id) =>
      `${obligation(propertyId, obligationId)}/payments/${paymentId}/reverse`,
    adjustments: (propertyId: Id, obligationId: Id) =>
      `${obligation(propertyId, obligationId)}/adjustments`,
    reverseAdjustment: (propertyId: Id, obligationId: Id, adjustmentId: Id) =>
      `${obligation(propertyId, obligationId)}/adjustments/${adjustmentId}/reverse`,
    claims: (propertyId: Id) => `${property(propertyId)}/payment-claims`,
    confirmClaim: (propertyId: Id, obligationId: Id, claimId: Id) =>
      `${obligation(propertyId, obligationId)}/payment-claims/${claimId}/confirm`,
    rejectClaim: (propertyId: Id, obligationId: Id, claimId: Id) =>
      `${obligation(propertyId, obligationId)}/payment-claims/${claimId}/reject`,
  },

  // Owner: notifications (rent.py)
  notifications: {
    list: `${API_PREFIX}/notifications`,
    markRead: (notificationId: Id) => `${API_PREFIX}/notifications/${notificationId}/read`,
  },

  // Owner: dashboard and reports (dashboard.py)
  dashboard: {
    rentDue: `${API_PREFIX}/dashboard/rent-due`,
    overview: `${API_PREFIX}/dashboard/overview`,
  },
  reports: {
    monthlyCollections: `${API_PREFIX}/reports/monthly-collections`,
    collectionTrends: `${API_PREFIX}/reports/collection-trends`,
    propertyRentSummary: `${API_PREFIX}/reports/property-rent-summary`,
    rentObligations: `${API_PREFIX}/reports/rent-obligations`,
  },

  // Owner: complaints (complaints.py)
  complaints: {
    list: (propertyId: Id) => `${property(propertyId)}/complaints`,
    summary: (propertyId: Id) => `${property(propertyId)}/complaints/summary`,
    detail: (propertyId: Id, complaintId: Id) =>
      `${property(propertyId)}/complaints/${complaintId}`,
    history: (propertyId: Id, complaintId: Id) =>
      `${property(propertyId)}/complaints/${complaintId}/history`,
    changeStatus: (propertyId: Id, complaintId: Id) =>
      `${property(propertyId)}/complaints/${complaintId}/status`,
  },

  // Owner: reviews (reviews.py)
  reviews: {
    list: `${API_PREFIX}/reviews`,
    byProperty: (propertyId: Id) => `${property(propertyId)}/reviews`,
    summary: (propertyId: Id) => `${property(propertyId)}/review-summary`,
    detail: (propertyId: Id, reviewId: Id) => `${property(propertyId)}/reviews/${reviewId}`,
    response: (propertyId: Id, reviewId: Id) =>
      `${property(propertyId)}/reviews/${reviewId}/response`,
  },

  // Tenant portal (tenant_portal.py, rent.py, complaints.py, reviews.py)
  tenantPortal: {
    bookingRequests: {
      list: `${TENANT_PORTAL}/booking-requests`,
      detail: (requestId: Id) => `${TENANT_PORTAL}/booking-requests/${requestId}`,
      cancel: (requestId: Id) => `${TENANT_PORTAL}/booking-requests/${requestId}/cancel`,
    },
    stays: {
      list: `${TENANT_PORTAL}/stays`,
      detail: (stayId: Id) => `${TENANT_PORTAL}/stays/${stayId}`,
    },
    bedChangeRequests: {
      list: `${TENANT_PORTAL}/bed-change-requests`,
      detail: (requestId: Id) => `${TENANT_PORTAL}/bed-change-requests/${requestId}`,
      cancel: (requestId: Id) => `${TENANT_PORTAL}/bed-change-requests/${requestId}/cancel`,
    },
    rentObligations: {
      list: `${TENANT_PORTAL}/rent-obligations`,
      detail: (obligationId: Id) => `${TENANT_PORTAL}/rent-obligations/${obligationId}`,
      submitClaim: (obligationId: Id) =>
        `${TENANT_PORTAL}/rent-obligations/${obligationId}/payment-claims`,
    },
    paymentClaims: {
      list: `${TENANT_PORTAL}/payment-claims`,
      detail: (claimId: Id) => `${TENANT_PORTAL}/payment-claims/${claimId}`,
    },
    notifications: {
      list: `${TENANT_PORTAL}/notifications`,
      markRead: (notificationId: Id) =>
        `${TENANT_PORTAL}/notifications/${notificationId}/read`,
    },
    complaints: {
      list: `${TENANT_PORTAL}/complaints`,
      summary: `${TENANT_PORTAL}/complaints/summary`,
      detail: (complaintId: Id) => `${TENANT_PORTAL}/complaints/${complaintId}`,
      history: (complaintId: Id) => `${TENANT_PORTAL}/complaints/${complaintId}/history`,
      confirmResolution: (complaintId: Id) =>
        `${TENANT_PORTAL}/complaints/${complaintId}/confirm-resolution`,
      rejectResolution: (complaintId: Id) =>
        `${TENANT_PORTAL}/complaints/${complaintId}/reject-resolution`,
    },
    reviews: {
      list: `${TENANT_PORTAL}/reviews`,
      detail: (reviewId: Id) => `${TENANT_PORTAL}/reviews/${reviewId}`,
    },
  },
} as const;
