module.exports = {
  defineTask: jest.fn(),
  isTaskDefined: jest.fn().mockReturnValue(false),
  isTaskRegisteredAsync: jest.fn().mockResolvedValue(false),
  unregisterAllTasksAsync: jest.fn().mockResolvedValue(undefined),
};
