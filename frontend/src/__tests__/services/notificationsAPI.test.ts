/**
 * notificationsAPI URL regression tests.
 *
 * Django routes the mark-read actions at the hyphenated URLs
 * ``mark-read/`` and ``mark-all-read/`` (set via @action url_path=...
 * in NotificationViewSet). Underscored variants 404 silently and leave
 * the bell badge stuck on a stale unread count.
 */
/* eslint-disable import/first */

jest.mock('axios', () => {
  const post = jest.fn().mockResolvedValue({ data: {} });
  const get = jest.fn().mockResolvedValue({ data: { results: [] } });
  const del = jest.fn().mockResolvedValue({ data: {} });
  const put = jest.fn().mockResolvedValue({ data: {} });
  const instance = {
    post,
    get,
    delete: del,
    put,
    interceptors: {
      request: { use: jest.fn() },
      response: { use: jest.fn() },
    },
    defaults: { headers: { common: {} } },
  };
  return {
    __esModule: true,
    default: {
      create: jest.fn(() => instance),
      post: jest.fn(),
      get: jest.fn(),
      __mockInstance: instance,
    },
  };
});

import axios from 'axios';
import { notificationsAPI } from '../../services/api';

const mockInstance = (axios as unknown as { __mockInstance: { post: jest.Mock } }).__mockInstance;

describe('notificationsAPI URL regression', () => {
  beforeEach(() => {
    mockInstance.post.mockClear();
  });

  it('markAsRead targets /notifications/<id>/mark-read/ (hyphens)', async () => {
    await notificationsAPI.markAsRead('abc');
    expect(mockInstance.post).toHaveBeenCalledWith('/notifications/abc/mark-read/');
  });

  it('markAllAsRead targets /notifications/mark-all-read/ (hyphens)', async () => {
    await notificationsAPI.markAllAsRead();
    expect(mockInstance.post).toHaveBeenCalledWith('/notifications/mark-all-read/');
  });
});
