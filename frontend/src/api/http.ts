import axios, { type AxiosInstance } from 'axios'

const http: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    // 统一异常约定：{code, message, data}，外层包 envelope
    const payload = error?.response?.data ?? { code: 'NETWORK_ERROR', message: error.message }
    return Promise.reject(payload)
  },
)

export default http
