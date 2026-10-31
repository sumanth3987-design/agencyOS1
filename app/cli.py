import click
from flask.cli import with_appcontext
from app.extensions import db
from app.models import User


@click.command("create-admin")
@click.option("--email", prompt="Admin email", help="Admin email address")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Admin password",
)
@click.option(
    "--name", default="Agency Owner", prompt="Full Name", help="Admin full name"
)
@with_appcontext
def create_admin_command(email, password, name):
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        existing_user.role = "admin"
        existing_user.set_password(password)
        existing_user.full_name = name
        db.session.commit()
        click.echo(f"Updated existing user {email} to Admin role successfully.")
        return

    admin = User(
        email=email,
        full_name=name,
        role="admin",
        is_active=True,
    )
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    click.echo(f"Successfully created new Admin user: {email}")