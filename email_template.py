reset_password_request_email = """
<html>
  <head></head>
  <body>
    <p>Dear user, </p>
    <p>You have requested to reset your password, please click <a href="http://www.tplinkcrm.com/reset?uid={}&token={}">here</a> to continue.</p>
    <br>
    <p>
        Best Regards, <br>
        Administration Team
    </p>
  </body>
</html>
"""

reset_password_notification_email = """
<html>
  <head></head>
  <body>
    <p>Dear user, </p>
    <p>Your password is now reset. If you didn't request this, please contact adminitrator. </p>
    <br>
    <p>
        Best Regards, <br>
        Administration Team
    </p>
  </body>
</html>
"""
            
task_create_email = """
<html>
  <head></head>
  <body>
    <p>Dear user, </p>
    <p>You have a new task assigned: <strong>{subject}</strong></p>
    {detail}
    <p><a href="http://www.tplinkcrm.com/task/edit/{task_id}">Click here to view this task</a></p>
    <p><a href="http://www.tplinkcrm.com/task/dashboard">Click here to access task dashboard</a></p>
    <br>
    <p>
        Best Regards, <br>
        Task Management Team
    </p>
  </body>
</html>
"""

task_status_email = """
<html>
  <head></head>
  <body>
    <p>Dear user, </p>
    <p>The status of the task: <strong>{subject}</strong> has been changed to <strong>{status}</strong></p>
    {detail}
    <p><a href="http://www.tplinkcrm.com/task/edit/{task_id}">Click here to view this task</a></p>
    <p><a href="http://www.tplinkcrm.com/task/dashboard">Click here to access task dashboard</a></p>
    <br>
    <p>
        Best Regards, <br>
        Task Management Team
    </p>
  </body>
</html>
"""

task_reminder_email = """
<html>
  <head></head>
  <body>
    <p>Dear user, </p>
    <p>This is a reminder for the task: <strong>{subject}</strong></p>
    {detail}
    <p><a href="http://www.tplinkcrm.com/task/edit/{task_id}">Click here to view this task</a></p>
    <p><a href="http://www.tplinkcrm.com/task/dashboard">Click here to access task dashboard</a></p>
    <br>
    <p>
        Best Regards, <br>
        Task Management Team
    </p>
  </body>
</html>
"""

