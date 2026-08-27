# Deployment Workflow

- **NEVER** make direct code modifications on the VPS (Virtual Private Server) via SSH.
- There is a CI/CD system in place. All changes MUST be made in the local repository first.
- To deploy changes, commit and push to the GitHub repository. The CI/CD pipeline will automatically handle the deployment to the VPS.
