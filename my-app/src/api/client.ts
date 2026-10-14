import axios from 'axios';

import { API_BASE_URL } from '@/config/env';

const REQUEST_TIMEOUT_MS = 15_000;

// Content-Type is intentionally not set here: Axios sets `application/json` automatically
// for plain-object bodies, and leaves FormData (multipart) requests to set their own.
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: REQUEST_TIMEOUT_MS,
  headers: {
    Accept: 'application/json',
  },
});

export default apiClient;
