// Reveal Animations
const revealEls = document.querySelectorAll('.reveal');

if (revealEls.length > 0) {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('reveal-in');
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.18 }
  );

  revealEls.forEach((el) => observer.observe(el));
}

// Plan Card 3D Effect
const cards = document.querySelectorAll('.plan-card');

cards.forEach((card) => {
  card.addEventListener('mousemove', (event) => {
    const rect = card.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    card.style.setProperty('--mouse-x', `${x}px`);
    card.style.setProperty('--mouse-y', `${y}px`);

    const rotateX = ((y / rect.height) - 0.5) * -6;
    const rotateY = ((x / rect.width) - 0.5) * 6;

    card.style.transform = `translateY(-10px) scale(1.02) rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
  });

  card.addEventListener('mouseleave', () => {
    card.style.transform = 'translateY(0) scale(1) rotateX(0deg) rotateY(0deg)';
  });
});

// Mobile Menu Toggle
const menuToggle = document.getElementById('menu-toggle');
const mobileMenu = document.getElementById('mobile-menu');
const body = document.body;

if (menuToggle && mobileMenu) {
  menuToggle.addEventListener('click', () => {
    const isExpanded = menuToggle.getAttribute('aria-expanded') === 'true';
    menuToggle.setAttribute('aria-expanded', !isExpanded);
    mobileMenu.classList.toggle('active');
    body.style.overflow = isExpanded ? 'auto' : 'hidden'; // Prevent scrolling when menu is open
  });

  // Close menu when clicking a link
  const mobileLinks = mobileMenu.querySelectorAll('a');
  mobileLinks.forEach(link => {
    link.addEventListener('click', () => {
      menuToggle.setAttribute('aria-expanded', 'false');
      mobileMenu.classList.remove('active');
      body.style.overflow = 'auto';
    });
  });
}

// Header Scrolled State
const header = document.querySelector('.site-header');
if (header) {
  window.addEventListener('scroll', () => {
    if (window.scrollY > 20) {
      header.classList.add('scrolled');
    } else {
      header.classList.remove('scrolled');
    }
  });
}

// Pricing Billing Toggle
const billingButtons = document.querySelectorAll('[data-billing-target]');
const billingPanels = document.querySelectorAll('[data-billing-panel]');
const toggleSlider = document.querySelector('.toggle-slider');

if (billingButtons.length > 0 && billingPanels.length > 0) {
  const setBillingPanel = (target) => {
    billingButtons.forEach((button) => {
      const isActive = button.dataset.billingTarget === target;
      button.classList.toggle('is-active', isActive);
      button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
      
      if (isActive && toggleSlider) {
        // Move slider based on target
        if (target === 'monthly') {
          toggleSlider.style.transform = 'translateX(0)';
        } else {
          toggleSlider.style.transform = 'translateX(100%)';
        }
      }
    });

    billingPanels.forEach((panel) => {
      const shouldShow = panel.dataset.billingPanel === target;
      panel.hidden = !shouldShow;
      panel.classList.toggle('is-active', shouldShow);
    });
  };

  billingButtons.forEach((button) => {
    button.addEventListener('click', () => setBillingPanel(button.dataset.billingTarget));
  });

  const activeButton = document.querySelector('[data-billing-target].is-active');
  setBillingPanel(activeButton?.dataset.billingTarget || 'monthly');
}
