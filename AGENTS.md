# Project Instructions

## Code Style
- There is a .devcontainer environment for editing and running this application in development mode.
- All changes have to pass pre-commit hooks and the github workflows via the act commands that are present on this system.
- This is for a makerspace -- the default action is open, and while there can be workflows on the admin side of things, consider general requests as unauthenticated unless there is a specific need to either acknowledge or take action on this alert.
- Most calls to actions will be either by wehook push to either discord or integration into slack.  Keep that in mind when receiving alerts about supplies being out or areas that may need attention from either the cleaning staff or the logistics/supply team.

## Architecture
- Follow the repository pattern
- Keep business logic in service layers
- Always provide a reasonable default when creating entries.  We want a good out of the box experience for both developers as well as for new users.