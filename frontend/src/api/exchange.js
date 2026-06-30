import client from './client'

export const exchangeApi = {
  list: () => client.get('/exchanges'),
  create: (data) => client.post('/exchanges', data),
  update: (id, data) => client.put(`/exchanges/${id}`, data),
  remove: (id) => client.delete(`/exchanges/${id}`),
  test: (id) => client.post(`/exchanges/${id}/test`),
  getBalance: (id) => client.get(`/exchanges/${id}/balance`),
}
